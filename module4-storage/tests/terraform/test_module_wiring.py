"""
Module 4 – Terraform Tests: Cross-Module Wiring

These tests parse the ACTUAL .tf files with python-hcl2 (a real HCL2
parser, not a regex hack) and verify that every module call in root
main.tf supplies a value for every required (no-default) variable
declared in that module's variables.tf.

This catches a class of bug that `terraform validate` ALSO catches (it
would fail with "Missing required argument"), but these tests run
without needing the terraform CLI installed, and they run fast enough to
be part of a normal pytest suite alongside the application code tests.

If you have the real terraform CLI available, the equivalent (and more
authoritative) check is:

    cd terraform && terraform init -backend=false && terraform validate

See TERRAFORM_TESTING.md for the full validation workflow.
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
    """
    Merge all .tf files in a directory into one dict of lists, the same
    shape python-hcl2 produces for a single file (each top-level block
    type like 'resource', 'variable', 'module' maps to a list of blocks).
    """
    merged: Dict[str, List[Dict[str, Any]]] = {}
    for tf_file in sorted(directory.glob("*.tf")):
        data = load_tf_file(tf_file)
        for key, blocks in data.items():
            merged.setdefault(key, []).extend(blocks)
    return merged


def get_required_variables(module_dir: Path) -> set[str]:
    """Return the set of variable names in module_dir that have NO default
    (i.e. the caller MUST supply a value)."""
    data = merge_tf_dir(module_dir)
    required = set()
    for var_block in data.get("variable", []):
        for var_name, var_def in var_block.items():
            if "default" not in var_def:
                required.add(var_name)
    return required


def get_root_module_calls() -> Dict[str, Dict[str, Any]]:
    """Return {module_call_name: {arg_name: raw_hcl_value, ...}, ...} from
    root main.tf's `module` blocks."""
    data = load_tf_file(TERRAFORM_ROOT / "main.tf")
    calls: Dict[str, Dict[str, Any]] = {}
    for module_block in data.get("module", []):
        for call_name, call_args in module_block.items():
            # Strip internal hcl2 bookkeeping keys
            clean_args = {
                k: v for k, v in call_args.items()
                if not k.startswith("__")
            }
            calls[call_name] = clean_args
    return calls


# ── Module source -> directory mapping (mirrors root main.tf) ────────────

ACTIVE_MODULE_CALLS = {
    "kms": "modules/kms",
    "security_groups": "modules/security_groups",
    "rds": "modules/rds",
    "elasticache": "modules/elasticache",
    "iam": "modules/iam",
}


class TestCrossModuleWiring:
    """
    For every module call ACTUALLY UNCOMMENTED in root main.tf, verify
    every required variable of the called module has a corresponding
    argument supplied in the call.

    This is exactly the class of error `terraform plan` would catch as
    'Error: Missing required argument' -- catching it here means you find
    out from `pytest`, in about 200ms, without needing AWS credentials or
    the terraform CLI.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def module_calls(cls) -> Dict[str, Dict[str, Any]]:
        return get_root_module_calls()

    @pytest.mark.parametrize("call_name,module_path", ACTIVE_MODULE_CALLS.items())
    def test_all_required_variables_supplied(self, module_calls, call_name, module_path):
        assert call_name in module_calls, (
            f"Expected an active 'module \"{call_name}\"' block in root main.tf, "
            f"but none was found. Did it get commented out or renamed?"
        )

        required = get_required_variables(TERRAFORM_ROOT / module_path)
        supplied = set(module_calls[call_name].keys()) - {"source", "version"}

        missing = required - supplied
        assert not missing, (
            f"module \"{call_name}\" ({module_path}) is missing required "
            f"argument(s): {sorted(missing)}. Root main.tf must pass a "
            f"value for every variable in {module_path}/variables.tf that "
            f"has no default."
        )

    def test_no_unknown_active_modules(self, module_calls):
        """
        Catches the inverse mistake: a module call exists in main.tf that
        isn't in our expected active set above (e.g. someone uncommented
        the ecs module but didn't update this test, or added a new module
        nobody reviewed for required-variable completeness).
        """
        unexpected = set(module_calls.keys()) - set(ACTIVE_MODULE_CALLS.keys())
        assert not unexpected, (
            f"Found module call(s) in root main.tf not covered by this "
            f"test's ACTIVE_MODULE_CALLS mapping: {sorted(unexpected)}. "
            f"Add them to ACTIVE_MODULE_CALLS in this test file (and make "
            f"sure their required variables are wired correctly) before "
            f"considering this a passing build."
        )


class TestModuleOutputsExist:
    """
    Verify that every module.X.Y reference used in root main.tf's outputs
    block (or in another module's input) actually corresponds to a real
    `output "Y"` block declared in module X.
    """

    def test_root_outputs_reference_real_module_outputs(self):
        root_data = load_tf_file(TERRAFORM_ROOT / "main.tf")
        output_blocks = root_data.get("output", [])

        for output_block in output_blocks:
            for output_name, output_def in output_block.items():
                value = output_def.get("value", [""])[0]
                if not isinstance(value, str) or not value.startswith("${module."):
                    continue
                # Parse "${module.rds.database_url}" -> module_name="rds", output_name="database_url"
                inner = value.removeprefix("${module.").removesuffix("}")
                parts = inner.split(".")
                if len(parts) != 2:
                    continue
                module_name, referenced_output = parts
                module_path = ACTIVE_MODULE_CALLS.get(module_name)
                if module_path is None:
                    continue  # not one of our tracked active modules

                module_data = merge_tf_dir(TERRAFORM_ROOT / module_path)
                declared_outputs = {
                    name
                    for out_block in module_data.get("output", [])
                    for name in out_block.keys()
                }
                assert referenced_output in declared_outputs, (
                    f"Root output \"{output_name}\" references "
                    f"module.{module_name}.{referenced_output}, but "
                    f"{module_path}/main.tf declares no such output. "
                    f"Declared outputs there: {sorted(declared_outputs)}"
                )
