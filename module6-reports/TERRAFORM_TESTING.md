# Module 6: Terraform Testing & Validation

Same tooling and approach as Module 4/5's equivalent docs (read those
first for the general background on why these specific tools are used
instead of the real terraform CLI, which isn't available in this
sandbox).

---

## 0. What's structurally different from Module 4/5 here

Like Module 5, Module 6 owns no VPC/RDS/Redis of its own -- both are
mostly `data` lookups against Module 4's already-deployed resources.
Unlike Module 5, **Module 6 does own one genuinely new piece of
infrastructure**: an S3 bucket (`modules/s3/`) for storing generated
report files, since neither Module 4 nor Module 5 has an equivalent
(Module 2/3's ingestion bucket is a separate bucket for a separate
purpose).

```
terraform/
├── main.tf                 data lookups (VPC, subnets, Module 4's RDS SG,
│                            Module 4's KMS alias, Module 4's DB secret)
│                            + active s3 + security_groups module calls
│                            + commented ecs/iam module calls (pending inputs)
├── variables.tf
├── dev.tfvars
└── modules/
    ├── s3/                 reports bucket: KMS encryption, versioning,
    │                       lifecycle (NAAC 7yr retention), public-access
    │                       block, SNS event notifications
    ├── security_groups/    module6_service SG + cross-stack RDS ingress rule
    ├── iam/                ECS task roles (S3 write + KMS GenerateDataKey,
    │                       wider than Module 5's read-only IAM)
    └── ecs/                cluster, task def, service (0.5 vCPU / 1GB --
                             no ML model, smaller than Module 4/5's tasks)
```

---

## 1. Tool 1: terraform-config-inspect

```bash
sudo apt-get install -y terraform-config-inspect
cd terraform
for dir in . modules/s3 modules/security_groups modules/iam modules/ecs; do
  terraform-config-inspect --json "$dir" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print('OK' if not data.get('diagnostics') else 'FAIL: ' + str(data['diagnostics']))
"
done
```

**Result: all 5 modules (root + 4 submodules) parse with zero
diagnostics.** No syntax errors found on the first pass this time
(Module 5's earlier HCL-interpolation gotcha was avoided by writing
variable descriptions without any literal `${}`-shaped substrings from
the start).

---

## 2. Tool 2: checkov

```bash
python3 -m venv /tmp/tf-tools-venv
/tmp/tf-tools-venv/bin/pip install -r ../requirements-terraform.txt
cd terraform
/tmp/tf-tools-venv/bin/checkov -d . --compact
```

### First run result

**54 passed, 4 failed, 0 skipped.**

| Finding | Resource | Status |
|---|---|---|
| CKV_AWS_300 (abort incomplete multipart uploads) | S3 lifecycle | Fixed -- added `abort_incomplete_multipart_upload { days_after_initiation = 7 }` |
| CKV2_AWS_62 (S3 event notifications) | S3 bucket | Fixed -- added a real SNS topic + `aws_s3_bucket_notification` on `ObjectCreated`, not just a token resource to satisfy the checker (genuinely useful: a hook for a future "your report is ready" notification feature) |
| CKV2_AWS_5 (SG not attached) | `aws_security_group.module6_service` | Documented, not suppressed -- same confirmed checkov bug + genuinely-pending-ECS situation as Module 5 |
| CKV_AWS_144 (cross-region replication) | S3 bucket | Documented, not enabled -- see 2.1 below |

### 2.1 -- Two findings NOT fixed, and why

**`CKV2_AWS_5`** is the same confirmed upstream checkov bug covered in
Module 4/5's `TERRAFORM_TESTING.md`: inline `#checkov:skip` doesn't
suppress this specific graph check, and the underlying situation
(`module6_service` SG genuinely isn't attached to anything yet, because
`modules/ecs` is commented out) is real, not a false positive here.

**`CKV_AWS_144`** (cross-region replication) is a deliberate scope
decision: NAAC compliance reports for SRM AP are a single-region,
single-institution workload with no multi-region disaster-recovery
requirement anywhere in the locked spec. Enabling CRR would double
storage cost and require a second KMS key + cross-region IAM role for a
durability guarantee (surviving an entire AWS region outage) that isn't
asked for. Versioning (which IS enabled) already protects against the
failure mode that actually matters here -- accidental overwrite or
deletion of a report. Notably, **the inline `#checkov:skip` directive
was tried here too and did NOT suppress this check** (confirmed
empirically, then removed rather than left in as dead/misleading code)
-- unlike `CKV2_AWS_50`/`CKV2_AWS_57` in Module 4, which the identical
skip syntax DOES suppress. This appears to be a different, non-graph
check that still doesn't honor the skip for reasons not fully
determined; rather than spend further time chasing a checkov quirk, the
finding is left as an honest, documented failure with the reasoning
directly above the resource in `modules/s3/main.tf`.

### Final state

```
Passed checks:  59
Failed checks:  2   (CKV2_AWS_5 -- confirmed tool bug + genuinely pending ECS wiring;
                     CKV_AWS_144 -- deliberate CRR scope decision, documented)
Skipped checks: 0
```

Compare to the first run: 54 passed / 4 failed. 2 distinct findings got
a real infrastructure fix (multipart-upload cleanup, event
notifications); the remaining 2 are explained, not hidden.

---

## 3. Tool 3: custom pytest suite (tests/terraform/)

### test_module_wiring.py (6 tests)

Verifies both currently-active module calls (`s3`, `security_groups`)
supply every required variable. **Verified this actually catches bugs**:
deliberately removed `kms_key_arn` and `access_log_bucket_id` from the
`s3` module call, re-ran, got `AssertionError: module "s3" (modules/s3)
is missing required argument(s): ['kms_key_arn']`, then restored the
file. Also includes `TestPendingModulesAreReadyToUncomment` (confirms
`modules/ecs`/`modules/iam` parse cleanly on their own) and a regression
guard pinning the locked 0.5 vCPU / 1GB ECS sizing.

### test_security_invariants.py (19 tests)

The standout test here is `test_logging_never_targets_self` --
**verified to catch a REAL AWS behavior, not just a style preference**:
AWS does not support delivering S3 access logs to a destination bucket
using SSE-KMS encryption (confirmed via AWS's own documentation, search
query: "S3 server access logging target bucket SSE-KMS encryption not
supported"). The reports bucket uses SSE-KMS, so a naive
self-referencing `target_bucket = aws_s3_bucket.reports.id` would
silently fail to deliver logs (or worse, appear to work in `terraform
apply` and only fail at actual log-delivery time). Verified this test
catches the regression: deliberately reintroduced the self-reference,
watched the test fail with a clear diff, reverted.

Other coverage: KMS encryption on the bucket + SNS topic + CloudWatch
log group, full public-access block, 7-year lifecycle expiration,
multipart-upload cleanup, the cross-stack SG rule pattern (same
standalone-resource-not-inline-block rationale as Module 5), IAM policy
scoped to the reports bucket ARN specifically (not `*`), and the same
hardcoded-credential + sensitive-variable scans as the other modules.

### Running it

```bash
python3 -m venv /tmp/tf-tools-venv
/tmp/tf-tools-venv/bin/pip install -r requirements-terraform.txt
/tmp/tf-tools-venv/bin/python -m pytest tests/terraform/ -v
```

**Result: 25/25 passed**, confirmed with the exact pinned versions in
`requirements-terraform.txt` from a completely fresh venv.

---

## 4. Running the REAL terraform CLI yourself

Same caveat as Module 4/5: the terraform binary isn't available in this
sandbox.

```bash
cd terraform
terraform init -backend=false
terraform validate

# Needs Module 4 already deployed in this environment (data lookups
# depend on it) plus real AWS credentials
terraform plan -var-file=dev.tfvars
```

---

## 5. Quick reference: regenerating this validation

```bash
python3 -m venv /tmp/tf-tools-venv
/tmp/tf-tools-venv/bin/pip install -r requirements-terraform.txt

sudo apt-get install -y terraform-config-inspect
cd terraform
for dir in . modules/*/; do
  terraform-config-inspect --json "$dir" | python3 -c "import json,sys; d=json.load(sys.stdin); print('$dir:', 'OK' if not d.get('diagnostics') else d['diagnostics'])"
done

/tmp/tf-tools-venv/bin/checkov -d . --compact

cd ..
/tmp/tf-tools-venv/bin/python -m pytest tests/terraform/ -v
```
