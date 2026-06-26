# Module 5: Terraform Testing & Validation

This documents how the Terraform in `terraform/` was validated, same
approach and tooling as Module 4's `TERRAFORM_TESTING.md` (read that one
first if you haven't -- this doc assumes familiarity with why these
specific tools are used instead of the real terraform CLI).

---

## 0. What's structurally different from Module 4 here

Module 4 owns real data infrastructure (RDS, ElastiCache) and its
Terraform creates that infrastructure from scratch. **Module 5 owns no
data infrastructure at all** -- per the locked architecture decision, it
reads Module 4's PostgreSQL and shares Module 4's Redis. So Module 5's
Terraform is almost entirely `data` lookups against Module 4's
already-deployed resources, plus:

- Module 5's own ECS Fargate compute layer (a single search-api service,
  unlike Module 4's two-service consumer+api split)
- A security group for that compute layer
- Two **standalone** `aws_security_group_rule` resources that grant
  Module 5 ingress into Module 4's existing RDS and Redis security
  groups -- added from Module 5's own Terraform state, without touching
  Module 4's state or code at all (see Section 3 for why this specific
  resource type matters)
- A small Secrets Manager wrapper around Module 4's Redis URL (Module 4
  doesn't currently wrap it in Secrets Manager itself -- see Section 3)

```
terraform/
├── main.tf                    data lookups (VPC, subnets, Module 4's RDS/Redis
│                               SGs, Module 4's KMS alias, Module 4's DB secret)
│                               + active security_groups module call
│                               + commented ecs/iam module calls (pending inputs)
├── variables.tf
├── dev.tfvars
└── modules/
    ├── security_groups/       module5_service SG + cross-stack SG rules
    ├── iam/                   ECS execution/task roles (no S3 access, unlike Module 4)
    └── ecs/                   cluster, task def, service, autoscaling,
                                CloudWatch log group, Redis URL secret wrapper
```

---

## 1. Tool 1: terraform-config-inspect

Same tool, same install command as Module 4:

```bash
sudo apt-get install -y terraform-config-inspect
```

```bash
cd terraform
for dir in . modules/security_groups modules/iam modules/ecs; do
  echo "=== $dir ==="
  terraform-config-inspect --json "$dir" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print('OK' if not data.get('diagnostics') else 'FAIL: ' + str(data['diagnostics']))
"
done
```

**Result: all 4 modules (root + 3 submodules) parse with zero
diagnostics** -- but not on the first try. This tool caught a real bug
while writing `modules/security_groups/variables.tf`: a variable
description contained the literal text `promptflow-rds-` followed by a
dollar-brace placeholder as documentation, not accounting for the fact
that HCL tries to interpolate that syntax even inside a plain string
value. The fix was to rewrite the description text to avoid that
substring entirely, rather than fight with escape sequences. Caught and
fixed before this ever reached checkov or the pytest suite -- exactly the
kind of error class this tool exists to catch fast, for free, without
needing AWS credentials.

---

## 2. Tool 2: checkov

```bash
python3 -m venv /tmp/tf-tools-venv
/tmp/tf-tools-venv/bin/pip install -r ../requirements-terraform.txt
cd terraform
/tmp/tf-tools-venv/bin/checkov -d . --compact
```

### First run result

**47 passed, 2 failed, 0 skipped.** Notably cleaner than Module 4's first
run (67 passed / 25 failed) -- because the security group, KMS, and
secrets patterns from Module 4's remediation were applied here from the
start (scoped egress, every rule has a description, KMS encryption on
the log group and the Redis secret wrapper) rather than discovered after
the fact.

The 2 findings:

| Finding | Resource | Status |
|---|---|---|
| CKV2_AWS_57 (Secrets Manager rotation) | aws_secretsmanager_secret.redis_url | Fixed via documented #checkov:skip (same gap as Module 4's db_credentials secret -- needs a rotation Lambda not yet provisioned) |
| CKV2_AWS_5 (SG not attached) | aws_security_group.module5_service | Documented, NOT suppressed (see 2.1 below) |

### 2.1 -- CKV2_AWS_5 is the same confirmed checkov bug as Module 4

Identical situation to Module 4's `module4_service` finding (see Module
4's `TERRAFORM_TESTING.md` Section 3 for the full writeup, including the
GitHub-issue evidence that inline `#checkov:skip` is silently ignored for
this specific graph check). Briefly:

- This finding is **genuinely true right now** -- `module5_service`
  isn't attached to anything, because `modules/ecs` (which would
  reference it via `service_security_group_id`) is intentionally
  commented out in root `main.tf` pending ECR/ALB/JWT-public-key inputs.
- It is **not** suppressed via `#checkov:skip`, because that doesn't work
  for this check regardless of wording -- confirmed empirically on
  Module 4, same checkov version, same check ID.
- A plain (non-directive) comment is left directly above the resource in
  `modules/security_groups/main.tf` explaining this, so it's clear from
  reading the code why checkov flags it and what resolves it.

### Final state

```
Passed checks:  47
Failed checks:  1   (CKV2_AWS_5 -- confirmed tool bug + genuinely pending ECS wiring)
Skipped checks: 1   (CKV2_AWS_57 -- documented rotation gap, confirmed showing
                     in checkov's own "Skipped" output with the suppress comment)
```

Verified the skip actually took effect (unlike the CKV2_AWS_5 attempts):

```
Check: CKV2_AWS_57: "Ensure Secrets Manager secrets should have automatic rotation enabled"
	SKIPPED for resource: aws_secretsmanager_secret.redis_url
	Suppress comment: Automatic rotation requires a rotation Lambda not yet
	provisioned; tracked as a follow-up alongside Module 4's identical gap
```

---

## 3. Two design decisions worth explaining explicitly

### 3.1 -- Why aws_security_group_rule, not inline ingress {} blocks

Module 5 needs to grant itself access to Module 4's existing RDS and
Redis security groups, but Module 5's Terraform state has no business
touching Module 4's `aws_security_group` resources directly (different
state, different lifecycle, potentially applied by different people at
different times).

The fix is to add the new rules as **standalone**
`aws_security_group_rule` resources rather than inline `ingress {}`
blocks nested inside an `aws_security_group` resource. This distinction
matters: an inline block tells Terraform "this is the *complete* rule
set for this security group" -- if both Module 4's state (which manages
the SG itself) and Module 5's state each tried to declare their own
*complete* inline rule set for the same SG, they'd permanently fight over
each other's rules on every plan/apply. A standalone
`aws_security_group_rule`, by contrast, manages exactly one rule and
coexists peacefully with rules declared anywhere else, including a
completely different Terraform state -- which is the supported pattern
for this exact cross-stack scenario.

`tests/terraform/test_security_invariants.py::test_cross_stack_rules_use_standalone_resource_not_inline`
locks this in.

### 3.2 -- Why Module 5 wraps Redis URL in its own Secrets Manager entry

Module 4's `modules/elasticache` only exposes the Redis URL as a
sensitive Terraform *output* -- it never writes it into Secrets Manager
(unlike the database credentials, which Module 4 does wrap in a real
secret). That's a real, documented gap in Module 4, not something Module
5 should silently route around by accepting it as a plain environment
variable in the ECS task definition (which would make the Redis AUTH
token visible in the ECS console and CloudTrail logs every time the task
definition is viewed).

Instead, `modules/ecs/main.tf` creates its own
`aws_secretsmanager_secret` (`/promptflow/${environment}/module5-redis-url`),
populated from a `var.redis_url` input (which you supply manually via
`terraform output -raw redis_url` from Module 4's stack -- see
`dev.tfvars`), encrypted with the same shared KMS key Module 4 uses. The
task definition then injects `REDIS_URL` via the `secrets` block, not
`environment`.

If Module 4 later adds its own proper secret for this, this wrapper
resource should be removed in favor of referencing that one directly --
flagged as a follow-up in both modules' docs.

`tests/terraform/test_security_invariants.py::test_redis_url_injected_via_secrets_not_environment`
locks this in -- and this was verified to actually catch the regression
it claims to: `{ name = "REDIS_URL", value = var.redis_url }` was
deliberately added to the task definition's plaintext `environment`
block, the test was re-run and failed with an explicit message naming
the problem, then the file was reverted.

---

## 4. Tool 3: custom pytest suite (tests/terraform/)

Same python-hcl2-based approach as Module 4, adapted to Module 5's
structure:

### test_module_wiring.py (5 tests)

- Verifies the one currently-active module call (`security_groups`)
  supplies every required variable. **Verified this actually catches
  bugs**: `module4_redis_sg_id` was deliberately deleted from the call,
  the suite was re-run, producing `AssertionError: module
  "security_groups" (modules/security_groups) is missing required
  argument(s): ['module4_redis_sg_id']`, then the file was restored.
- `TestPendingModulesAreReadyToUncomment` -- confirms `modules/ecs` and
  `modules/iam` (built but not yet wired into root `main.tf`) are
  internally well-formed HCL on their own, so uncommenting them later is
  purely a wiring exercise.
- `test_ecs_module_declares_locked_sizing_defaults` -- pins the locked 1
  vCPU / 2GB sizing decision as a literal assertion against
  `modules/ecs/variables.tf`'s defaults, so a future "let's bump this
  while debugging" change doesn't silently become permanent.

### test_security_invariants.py (13 tests)

Covers the cross-stack rule pattern (3.1), the Redis secret-wrapping
pattern (3.2), KMS usage on the log group and Redis secret, scoped
egress on `module5_service`, every SG rule having a description, and the
same hardcoded-credential + sensitive-variable scans as Module 4's
equivalent file (with one Module-5-specific addition:
`test_redis_url_variable_is_sensitive`, since that's the one variable in
this codebase most likely to carry a real credential if the
`sensitive = true` marking is ever accidentally dropped).

### Running it

```bash
python3 -m venv /tmp/tf-tools-venv
/tmp/tf-tools-venv/bin/pip install -r requirements-terraform.txt
/tmp/tf-tools-venv/bin/python -m pytest tests/terraform/ -v
```

**Result: 18/18 passed**, confirmed with the exact pinned versions in
`requirements-terraform.txt` from a completely fresh venv (not just
whatever happened to already be installed).

---

## 5. Running the REAL terraform CLI yourself

Same caveat as Module 4: the terraform binary isn't available in this
sandbox. Once you have it locally:

```bash
cd terraform
terraform init -backend=false
terraform validate

# Full plan (needs real AWS credentials + Module 4 already deployed,
# since the data lookups in main.tf will fail if Module 4's VPC/RDS/Redis
# security groups/KMS alias/Secrets Manager entry don't already exist)
terraform plan -var-file=dev.tfvars
```

If the data lookups fail with "no matching resource found", it almost
always means Module 4 hasn't been applied yet in that environment, or
the `environment` variable value doesn't match between the two stacks
(Module 5's `data "aws_vpc"` filter is built from
`promptflow-${var.environment}-vpc` -- if Module 4 was applied with
`environment=dev` but Module 5 with `environment=staging`, the lookup
will correctly fail to find anything).

---

## 6. Quick reference: regenerating this validation

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
