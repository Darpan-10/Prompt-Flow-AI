"""
Module 5 – Terraform Tests: Security Invariants

Locks in the security properties confirmed by checkov (see
TERRAFORM_TESTING.md for the full before/after) as regression tests, the
same pattern as Module 4's tests/terraform/test_security_invariants.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import hcl2
import pytest

TERRAFORM_ROOT = Path(__file__).parent.parent.parent / "terraform"


def load_tf_file(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        return hcl2.load(f)


def merge_tf_dir(directory: Path) -> Dict[str, List[Dict[str, Any]]]:
    merged: Dict[str, List[Dict[str, Any]]] = {}
    for tf_file in sorted(directory.glob("*.tf")):
        data = load_tf_file(tf_file)
        for key, blocks in data.items():
            merged.setdefault(key, []).extend(blocks)
    return merged


def find_resource(data: Dict[str, Any], resource_type: str, resource_name: str) -> Dict[str, Any]:
    for res_block in data.get("resource", []):
        if resource_type in res_block and resource_name in res_block[resource_type]:
            return res_block[resource_type][resource_name]
    raise AssertionError(f'resource "{resource_type}" "{resource_name}" not found')


def find_all_blocks_of_type(body: Dict[str, Any], block_key: str) -> List[Dict[str, Any]]:
    val = body.get(block_key, [])
    if isinstance(val, dict):
        return [val]
    return val


class TestSecurityGroupInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def sg_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/security_groups")

    def test_module5_service_egress_has_no_all_ports_rule(self, sg_data):
        """
        Regression guard for CKV_AWS_382-style findings: module5_service
        must never have an egress rule spanning ALL ports/protocols
        (from_port=0, to_port=0, protocol=-1). Egress must stay scoped to
        the specific ports the service actually needs (443, 5432, 6379, 53).
        """
        body = find_resource(sg_data, "aws_security_group", "module5_service")
        for egress in find_all_blocks_of_type(body, "egress"):
            protocol = egress.get("protocol", [""])[0]
            from_port = egress.get("from_port", [None])[0]
            to_port = egress.get("to_port", [None])[0]
            is_all_ports_all_protocols = protocol == "-1" and from_port == 0 and to_port == 0
            assert not is_all_ports_all_protocols, (
                'aws_security_group "module5_service" has an egress rule '
                "spanning all ports/protocols. Egress should stay scoped "
                "to specific ports -- see the rationale comment in "
                "modules/security_groups/main.tf."
            )

    def test_module5_service_egress_rule_count(self, sg_data):
        """Sanity check egress wasn't accidentally collapsed back down to
        one broad rule."""
        body = find_resource(sg_data, "aws_security_group", "module5_service")
        egress_rules = find_all_blocks_of_type(body, "egress")
        assert len(egress_rules) >= 4, (  # 443, 5432, 6379, 53
            f"Expected at least 4 distinct scoped egress rules on "
            f"module5_service, found {len(egress_rules)}."
        )

    def test_every_ingress_and_egress_rule_has_a_description(self, sg_data):
        """Regression guard for CKV_AWS_23."""
        body = find_resource(sg_data, "aws_security_group", "module5_service")
        for block_key in ("ingress", "egress"):
            for rule in find_all_blocks_of_type(body, block_key):
                assert rule.get("description"), (
                    f'A {block_key} rule on "module5_service" has no '
                    f"description. Every rule must explain what it's for."
                )

    def test_cross_stack_rules_use_standalone_resource_not_inline(self, sg_data):
        """
        Critical structural guard: the rules granting Module 5 access to
        Module 4's RDS/Redis security groups MUST be standalone
        aws_security_group_rule resources, never inline ingress/egress
        blocks inside a resource that also belongs to Module 4's state.
        Mixing the two would cause perpetual plan diffs between the two
        independently-applied Terraform states.
        """
        rds_rule = find_resource(sg_data, "aws_security_group_rule", "module5_to_rds")
        assert rds_rule.get("type") == ["ingress"]
        assert rds_rule.get("from_port") == [5432]

        redis_rule = find_resource(sg_data, "aws_security_group_rule", "module5_to_redis")
        assert redis_rule.get("type") == ["ingress"]
        assert redis_rule.get("from_port") == [6379]

    def test_cross_stack_rules_source_from_module5_sg_not_a_cidr(self, sg_data):
        """
        The cross-stack rules must use source_security_group_id
        (referencing Module 5's own SG), NOT cidr_blocks. Falling back to
        a CIDR range here would widen RDS/Redis access to anything in
        that range, not just Module 5's actual ECS tasks.
        """
        for rule_name in ("module5_to_rds", "module5_to_redis"):
            rule = find_resource(sg_data, "aws_security_group_rule", rule_name)
            assert "source_security_group_id" in rule, (
                f'"{rule_name}" must use source_security_group_id, not cidr_blocks'
            )
            assert "cidr_blocks" not in rule


class TestECSModuleSecurityInvariants:
    """
    These validate modules/ecs even though it's not currently wired into
    root main.tf (it's commented out pending deployment inputs) -- the
    module itself should already be correct so that uncommenting it later
    is a pure wiring exercise, not a "discover new bugs" exercise.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def ecs_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/ecs")

    def test_log_group_uses_kms_key(self, ecs_data):
        body = find_resource(ecs_data, "aws_cloudwatch_log_group", "search_api")
        assert body.get("kms_key_id") == ["${var.kms_key_arn}"]

    def test_redis_secret_uses_kms_key(self, ecs_data):
        body = find_resource(ecs_data, "aws_secretsmanager_secret", "redis_url")
        assert body.get("kms_key_id") == ["${var.kms_key_arn}"]

    def test_redis_url_injected_via_secrets_not_environment(self, ecs_data):
        """
        Critical guard: REDIS_URL (which contains the Redis AUTH token)
        must be injected via the task definition's `secrets` block
        (resolved from Secrets Manager at task start), never via the
        plaintext `environment` block where it would be visible in the
        ECS console and CloudTrail.
        """
        for res_block in ecs_data.get("resource", []):
            if "aws_ecs_task_definition" not in res_block:
                continue
            for _, body in res_block["aws_ecs_task_definition"].items():
                container_defs_raw = body.get("container_definitions", [""])[0]
                # container_definitions is a jsonencode(...) expression, a
                # string in the parsed HCL, not real JSON we can re-parse
                # generically -- check at the textual level instead.
                assert "REDIS_URL" not in _extract_environment_block_text(container_defs_raw), (
                    "REDIS_URL must not appear in the task definition's "
                    "plaintext `environment` block -- it belongs in "
                    "`secrets`, resolved from Secrets Manager."
                )
                assert "REDIS_URL" in container_defs_raw, (
                    "Expected REDIS_URL to be referenced somewhere in the "
                    "container definition (via the secrets block)."
                )

    def test_database_url_injected_via_secrets(self, ecs_data):
        for res_block in ecs_data.get("resource", []):
            if "aws_ecs_task_definition" not in res_block:
                continue
            for _, body in res_block["aws_ecs_task_definition"].items():
                container_defs_raw = body.get("container_definitions", [""])[0]
                assert "DATABASE_URL" in container_defs_raw

    def test_locked_sizing_not_accidentally_widened_to_full_vcpu_table(self, ecs_data):
        """
        Loose guard against a common mistake: someone bumps cpu/memory
        "while debugging" using a much larger Fargate size (e.g. 4096/8192)
        and forgets to revert. This doesn't pin the EXACT locked values
        (that's covered in test_module_wiring.py against variables.tf
        defaults) -- it just catches an order-of-magnitude regression if
        the *task definition* ever hardcodes something different from
        what the variable defaults imply.
        """
        body = find_resource(ecs_data, "aws_ecs_task_definition", "search_api")
        assert body.get("cpu") == ["${var.task_cpu}"]
        assert body.get("memory") == ["${var.task_memory}"]


class TestNoHardcodedSecrets:
    SUSPICIOUS_PATTERNS = [
        "AKIA",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
    ]

    def test_no_aws_credential_patterns_in_any_tf_file(self):
        offending = []
        for tf_file in TERRAFORM_ROOT.rglob("*.tf"):
            text = tf_file.read_text()
            for pattern in self.SUSPICIOUS_PATTERNS:
                if pattern in text:
                    offending.append((str(tf_file), pattern))
        assert not offending, f"Found suspicious hardcoded credential patterns: {offending}"

    def test_password_and_token_variables_are_marked_sensitive(self):
        """Same heuristic as Module 4's equivalent test -- excludes
        *_arn variables, since an ARN is an identifier, not secret
        material."""
        secret_name_hints = ("password", "token", "private_key")
        offending = []
        for tf_file in TERRAFORM_ROOT.rglob("variables.tf"):
            data = load_tf_file(tf_file)
            for var_block in data.get("variable", []):
                for var_name, var_def in var_block.items():
                    if var_name.lower().endswith("_arn"):
                        continue
                    if any(hint in var_name.lower() for hint in secret_name_hints):
                        if var_def.get("sensitive") != [True]:
                            offending.append(f"{tf_file}:{var_name}")
        assert not offending, (
            f"Variables that look like secret MATERIAL but aren't marked "
            f"sensitive=true: {offending}"
        )

    def test_redis_url_variable_is_sensitive(self):
        """Specific guard for the one var most likely to leak a real
        credential if this slips: redis_url embeds the Redis AUTH token."""
        for tf_file in TERRAFORM_ROOT.rglob("variables.tf"):
            data = load_tf_file(tf_file)
            for var_block in data.get("variable", []):
                if "redis_url" in var_block:
                    assert var_block["redis_url"].get("sensitive") == [True], (
                        f"redis_url in {tf_file} must be marked sensitive=true"
                    )


def _extract_environment_block_text(container_definitions_expr: str) -> str:
    """
    container_definitions is `jsonencode([{ ... environment = [...] ...}])`
    in the HCL source -- python-hcl2 gives us this as a raw string
    (the unparsed jsonencode(...) expression text), not structured JSON,
    since the values inside reference Terraform expressions like
    var.environment that aren't valid JSON literals. Extract just the
    `environment = [...]` portion textually for a targeted check rather
    than trying to fully parse the expression.
    """
    start = container_definitions_expr.find("environment")
    end = container_definitions_expr.find("secrets")
    if start == -1 or end == -1:
        return ""
    return container_definitions_expr[start:end]
