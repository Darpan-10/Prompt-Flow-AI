"""
Module 6 – Terraform Tests: Security Invariants

Locks in the checkov-driven fixes as regressions. Same pattern as
Module 4/5's equivalent files.
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


class TestS3SecurityInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def s3_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/s3")

    def test_bucket_encrypted_with_kms(self, s3_data):
        body = find_resource(s3_data, "aws_s3_bucket_server_side_encryption_configuration", "reports")
        rules = find_all_blocks_of_type(body, "rule")
        assert len(rules) == 1
        default = rules[0].get("apply_server_side_encryption_by_default", [{}])[0]
        assert default.get("sse_algorithm") == ["aws:kms"]
        assert default.get("kms_master_key_id") == ["${var.kms_key_arn}"]

    def test_versioning_enabled(self, s3_data):
        body = find_resource(s3_data, "aws_s3_bucket_versioning", "reports")
        config = body.get("versioning_configuration", [{}])[0]
        assert config.get("status") == ["Enabled"]

    def test_public_access_fully_blocked(self, s3_data):
        body = find_resource(s3_data, "aws_s3_bucket_public_access_block", "reports")
        assert body.get("block_public_acls") == [True]
        assert body.get("block_public_policy") == [True]
        assert body.get("ignore_public_acls") == [True]
        assert body.get("restrict_public_buckets") == [True]

    def test_lifecycle_has_naac_7yr_expiration(self, s3_data):
        body = find_resource(s3_data, "aws_s3_bucket_lifecycle_configuration", "reports")
        rules = find_all_blocks_of_type(body, "rule")
        assert len(rules) == 1
        rule = rules[0]
        expiration = rule.get("expiration", [{}])[0]
        assert expiration.get("days") == [2557]  # 7 years

    def test_lifecycle_aborts_incomplete_multipart_uploads(self, s3_data):
        """Regression guard for CKV_AWS_300."""
        body = find_resource(s3_data, "aws_s3_bucket_lifecycle_configuration", "reports")
        rules = find_all_blocks_of_type(body, "rule")
        rule = rules[0]
        abort_config = rule.get("abort_incomplete_multipart_upload", [{}])[0]
        assert abort_config.get("days_after_initiation") == [7]

    def test_logging_never_targets_self(self, s3_data):
        """
        CRITICAL regression guard: AWS does not support S3 access log
        delivery to a SSE-KMS-encrypted destination bucket. This bucket
        uses SSE-KMS (see test_bucket_encrypted_with_kms above), so
        aws_s3_bucket_logging.target_bucket must NEVER be set to this
        same bucket's own id/reports.reports self-reference -- that
        would silently fail. It must only reference the external
        var.access_log_bucket_id input.
        """
        body = find_resource(s3_data, "aws_s3_bucket_logging", "reports")
        target = body.get("target_bucket", [""])[0]
        assert target == "${var.access_log_bucket_id}", (
            f"target_bucket must reference var.access_log_bucket_id (an "
            f"external, separately-managed SSE-S3 bucket), got: {target!r}. "
            f"Referencing this same KMS-encrypted bucket would silently "
            f"fail to deliver logs."
        )

    def test_logging_is_conditional_not_unconditional(self, s3_data):
        """The logging resource must have a count/for_each guard so it's
        skippable when no logging bucket is configured yet -- it must
        NOT be an unconditional resource that would fail apply with an
        empty target_bucket."""
        body = find_resource(s3_data, "aws_s3_bucket_logging", "reports")
        assert "count" in body or "for_each" in body

    def test_event_notification_configured(self, s3_data):
        """Regression guard for CKV2_AWS_62."""
        body = find_resource(s3_data, "aws_s3_bucket_notification", "reports")
        topics = find_all_blocks_of_type(body, "topic")
        assert len(topics) == 1
        assert "s3:ObjectCreated:*" in topics[0].get("events", [[]])[0]

    def test_sns_topic_encrypted(self, s3_data):
        body = find_resource(s3_data, "aws_sns_topic", "report_events")
        assert body.get("kms_master_key_id") == ["${var.kms_key_arn}"]


class TestSecurityGroupInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def sg_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/security_groups")

    def test_module6_service_egress_has_no_all_ports_rule(self, sg_data):
        body = find_resource(sg_data, "aws_security_group", "module6_service")
        for egress in find_all_blocks_of_type(body, "egress"):
            protocol = egress.get("protocol", [""])[0]
            from_port = egress.get("from_port", [None])[0]
            to_port = egress.get("to_port", [None])[0]
            is_all = protocol == "-1" and from_port == 0 and to_port == 0
            assert not is_all, "module6_service must not have an all-ports egress rule"

    def test_every_rule_has_a_description(self, sg_data):
        body = find_resource(sg_data, "aws_security_group", "module6_service")
        for block_key in ("ingress", "egress"):
            for rule in find_all_blocks_of_type(body, block_key):
                assert rule.get("description")

    def test_cross_stack_rule_is_standalone_resource(self, sg_data):
        rule = find_resource(sg_data, "aws_security_group_rule", "module6_to_rds")
        assert rule.get("type") == ["ingress"]
        assert rule.get("from_port") == [5432]

    def test_cross_stack_rule_sources_from_own_sg_not_cidr(self, sg_data):
        rule = find_resource(sg_data, "aws_security_group_rule", "module6_to_rds")
        assert "source_security_group_id" in rule
        assert "cidr_blocks" not in rule


class TestECSModuleSecurityInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def ecs_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/ecs")

    def test_log_group_uses_kms_key(self, ecs_data):
        body = find_resource(ecs_data, "aws_cloudwatch_log_group", "reports_api")
        assert body.get("kms_key_id") == ["${var.kms_key_arn}"]

    def test_log_retention_at_least_one_year(self, ecs_data):
        body = find_resource(ecs_data, "aws_cloudwatch_log_group", "reports_api")
        assert body.get("retention_in_days") == [400]

    def test_database_url_injected_via_secrets_not_environment(self, ecs_data):
        body = find_resource(ecs_data, "aws_ecs_task_definition", "reports_api")
        container_defs_raw = body.get("container_definitions", [""])[0]
        env_section = _extract_environment_block_text(container_defs_raw)
        assert "DATABASE_URL" not in env_section
        assert "DATABASE_URL" in container_defs_raw  # present via secrets block


class TestIamSecurityInvariants:
    @pytest.fixture(scope="class")
    @classmethod
    def iam_data(cls) -> Dict[str, Any]:
        return merge_tf_dir(TERRAFORM_ROOT / "modules/iam")

    def test_task_policy_scoped_to_reports_bucket_only(self, iam_data):
        """The S3 permissions must be scoped to the reports bucket ARN,
        not '*' or some other bucket's ARN."""
        body = find_resource(iam_data, "aws_iam_role_policy", "module6_task_access")
        policy_json = body.get("policy", [""])[0]
        assert "var.reports_bucket_arn" in policy_json
        assert '"Resource": "*"' not in policy_json.replace(" ", "")


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
        assert not offending, f"Variables that look like secret material but aren't marked sensitive=true: {offending}"


def _extract_environment_block_text(container_definitions_expr: str) -> str:
    start = container_definitions_expr.find("environment")
    end = container_definitions_expr.find("secrets")
    if start == -1 or end == -1:
        return ""
    return container_definitions_expr[start:end]
