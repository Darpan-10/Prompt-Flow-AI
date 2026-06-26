"""
Module 4 – Terraform Tests: Security Invariants

These tests lock in the security fixes made in response to the checkov
scan (see TERRAFORM_TESTING.md for the full before/after). They exist so
that a future edit -- made without re-running checkov -- can't silently
reintroduce one of these specific regressions (e.g. someone "simplifies"
a security group by widening egress back to 0.0.0.0/0 on all ports).

These are intentionally narrow and explicit rather than a generic
"re-run checkov" gate, because:
  1. They run in plain pytest, no checkov/terraform binary required.
  2. Each failure message says exactly what regressed and why it matters.
  3. New findings from a future checkov run should get their OWN test
     added here once fixed, the same way these were -- this file is
     meant to grow.
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
    """Find a specific `resource "type" "name" { ... }` block's body."""
    for res_block in data.get("resource", []):
        if resource_type in res_block and resource_name in res_block[resource_type]:
            return res_block[resource_type][resource_name]
    raise AssertionError(f'resource "{resource_type}" "{resource_name}" not found')


def find_all_blocks_of_type(body: Dict[str, Any], block_key: str) -> List[Dict[str, Any]]:
    """
    A repeated block like `egress { ... }` appearing multiple times in one
    resource is represented by hcl2 as a list under that key (or a single
    dict if it appears once). Normalize to always return a list.
    """
    val = body.get(block_key, [])
    if isinstance(val, dict):
        return [val]
    return val


class TestRDSecurityInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def rds_body(cls) -> Dict[str, Any]:
        data = merge_tf_dir(TERRAFORM_ROOT / "modules/rds")
        return find_resource(data, "aws_db_instance", "promptflow")

    def test_storage_is_encrypted(self, rds_body):
        assert rds_body.get("storage_encrypted") == [True]

    def test_storage_uses_customer_managed_kms_key(self, rds_body):
        """Regression guard for the checkov CKV_AWS_149-adjacent fix --
        RDS storage must use the shared CMK, not the AWS-default key."""
        kms_key_id = rds_body.get("kms_key_id")
        assert kms_key_id is not None, "RDS instance must set kms_key_id (use the shared CMK, not AWS-default encryption)"
        assert kms_key_id == ["${var.kms_key_arn}"]

    def test_not_publicly_accessible(self, rds_body):
        assert rds_body.get("publicly_accessible") == [False]

    def test_iam_database_authentication_enabled(self, rds_body):
        """Regression guard for CKV_AWS_161."""
        assert rds_body.get("iam_database_authentication_enabled") == [True]

    def test_auto_minor_version_upgrade_enabled(self, rds_body):
        """Regression guard for CKV_AWS_226 -- security patches apply automatically."""
        assert rds_body.get("auto_minor_version_upgrade") == [True]

    def test_enhanced_monitoring_configured(self, rds_body):
        """Regression guard for CKV_AWS_118."""
        assert rds_body.get("monitoring_interval") == [60]
        assert "monitoring_role_arn" in rds_body

    def test_performance_insights_uses_kms_key(self, rds_body):
        """Regression guard for CKV_AWS_354."""
        assert rds_body.get("performance_insights_kms_key_id") == ["${var.kms_key_arn}"]

    def test_force_ssl_parameter_present(self):
        """Regression guard for CKV2_AWS_69 -- encryption in transit enforced
        via the rds.force_ssl parameter group setting."""
        data = merge_tf_dir(TERRAFORM_ROOT / "modules/rds")
        pg_blocks = data.get("resource", [])
        found = False
        for res_block in pg_blocks:
            if "aws_db_parameter_group" not in res_block:
                continue
            for _, body in res_block["aws_db_parameter_group"].items():
                for param in find_all_blocks_of_type(body, "parameter"):
                    if param.get("name") == ["rds.force_ssl"]:
                        found = True
                        assert param.get("value") == ["1"]
        assert found, "Expected a 'rds.force_ssl' parameter set to '1' in the DB parameter group"


class TestSecretsManagerSecurityInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def secret_body(cls) -> Dict[str, Any]:
        data = merge_tf_dir(TERRAFORM_ROOT / "modules/rds")
        return find_resource(data, "aws_secretsmanager_secret", "db_credentials")

    def test_uses_customer_managed_kms_key(self, secret_body):
        """Regression guard for CKV_AWS_149."""
        assert secret_body.get("kms_key_id") == ["${var.kms_key_arn}"]


class TestElastiCacheSecurityInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def redis_body(cls) -> Dict[str, Any]:
        data = merge_tf_dir(TERRAFORM_ROOT / "modules/elasticache")
        return find_resource(data, "aws_elasticache_replication_group", "promptflow")

    def test_at_rest_encryption_enabled(self, redis_body):
        assert redis_body.get("at_rest_encryption_enabled") == [True]

    def test_transit_encryption_enabled(self, redis_body):
        assert redis_body.get("transit_encryption_enabled") == [True]

    def test_uses_customer_managed_kms_key(self, redis_body):
        """Regression guard for CKV_AWS_191."""
        assert redis_body.get("kms_key_id") == ["${var.kms_key_arn}"]

    def test_auth_token_required(self, redis_body):
        assert "auth_token" in redis_body


class TestSecurityGroupInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def sg_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/security_groups")

    def _get_sg_body(self, sg_data, name: str) -> Dict[str, Any]:
        return find_resource(sg_data, "aws_security_group", name)

    @pytest.mark.parametrize("sg_name", ["rds", "redis"])
    def test_db_tier_egress_never_open_to_internet(self, sg_data, sg_name):
        """
        Regression guard for CKV_AWS_382. The rds and redis security
        groups must NEVER have an egress rule with cidr_blocks containing
        0.0.0.0/0 -- these tiers should only ever talk within the VPC.

        This is the single highest-value regression test in this file:
        it's exactly the kind of "helpful simplification" (widening
        egress back to 0.0.0.0/0/-1) that's easy to make by accident
        while debugging connectivity issues, and easy to forget to
        revert.
        """
        body = self._get_sg_body(sg_data, sg_name)
        for egress in find_all_blocks_of_type(body, "egress"):
            cidrs = egress.get("cidr_blocks", [[]])
            flat_cidrs = cidrs[0] if isinstance(cidrs[0], list) else cidrs
            assert "0.0.0.0/0" not in flat_cidrs, (
                f'aws_security_group "{sg_name}" has an egress rule open to '
                f"0.0.0.0/0. The {sg_name} tier must only egress within the "
                f"VPC (var.vpc_cidr) -- it has no legitimate reason to reach "
                f"the public internet."
            )

    def test_module4_service_egress_has_no_all_ports_rule(self, sg_data):
        """
        Regression guard for CKV_AWS_382 on the compute tier. Unlike the
        DB tier, module4_service DOES need internet egress (HTTPS to AWS
        APIs), but it should never have a rule spanning ALL ports
        (from_port=0, to_port=0, protocol=-1) -- that defeats the purpose
        of having scoped rules at all.
        """
        body = self._get_sg_body(sg_data, "module4_service")
        for egress in find_all_blocks_of_type(body, "egress"):
            protocol = egress.get("protocol", [""])[0]
            from_port = egress.get("from_port", [None])[0]
            to_port = egress.get("to_port", [None])[0]
            is_all_ports_all_protocols = (
                protocol == "-1" and from_port == 0 and to_port == 0
            )
            assert not is_all_ports_all_protocols, (
                'aws_security_group "module4_service" has an egress rule '
                "spanning all ports/protocols. Egress should be scoped to "
                "specific ports (443, 5432, 6379, 9092-9096, 53) -- see "
                "the rationale comment in modules/security_groups/main.tf."
            )

    @pytest.mark.parametrize("sg_name,expected_port_count", [
        ("module4_service", 5),  # 443, 5432, 6379, 9092-9096, 53
    ])
    def test_module4_service_egress_rule_count(self, sg_data, sg_name, expected_port_count):
        """Sanity check that egress wasn't accidentally collapsed back
        down to a single broad rule (count regression, complementary to
        the all-ports check above)."""
        body = self._get_sg_body(sg_data, sg_name)
        egress_rules = find_all_blocks_of_type(body, "egress")
        assert len(egress_rules) >= expected_port_count, (
            f'Expected at least {expected_port_count} distinct scoped '
            f'egress rules on "{sg_name}", found {len(egress_rules)}.'
        )

    @pytest.mark.parametrize("sg_name", ["module4_service", "rds", "redis"])
    def test_every_ingress_and_egress_rule_has_a_description(self, sg_data, sg_name):
        """Regression guard for CKV_AWS_23 -- every rule should be
        self-documenting for audit purposes."""
        body = self._get_sg_body(sg_data, sg_name)
        for block_key in ("ingress", "egress"):
            for rule in find_all_blocks_of_type(body, block_key):
                assert rule.get("description"), (
                    f'A {block_key} rule on "{sg_name}" has no description. '
                    f"Every ingress/egress rule must explain what it's for."
                )


class TestNoHardcodedSecrets:
    """
    Defense-in-depth scan: no .tf file should contain anything that looks
    like a hardcoded AWS key, password literal, or token -- everything
    sensitive must flow through variables (ultimately backed by
    Secrets Manager / environment-injected tfvars, never committed).
    """

    SUSPICIOUS_PATTERNS = [
        "AKIA",  # AWS access key ID prefix
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
        """
        Every variable whose name suggests it holds actual secret
        MATERIAL (a password, token, or key) must have sensitive = true,
        so `terraform plan`/`apply` output never echoes it.

        Variables ending in "_arn" are deliberately excluded: an ARN is
        an identifier that POINTS AT a Secrets Manager secret, it is not
        the secret value itself -- db_secret_arn and redis_secret_arn are
        the canonical examples in this codebase. Marking an ARN sensitive
        would just hide a harmless resource locator from plan output for
        no real security benefit, and reading the actual secret value
        still requires separate IAM permission to call
        secretsmanager:GetSecretValue regardless of whether the ARN
        itself was ever displayed.
        """
        secret_name_hints = ("password", "token", "private_key")
        offending = []
        for tf_file in TERRAFORM_ROOT.rglob("variables.tf"):
            data = load_tf_file(tf_file)
            for var_block in data.get("variable", []):
                for var_name, var_def in var_block.items():
                    if var_name.lower().endswith("_arn"):
                        continue  # ARNs are identifiers, not secret material
                    if any(hint in var_name.lower() for hint in secret_name_hints):
                        if var_def.get("sensitive") != [True]:
                            offending.append(f"{tf_file}:{var_name}")
        assert not offending, (
            f"Variables that look like secret MATERIAL but aren't marked "
            f"sensitive=true: {offending}"
        )
