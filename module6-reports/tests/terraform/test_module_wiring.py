"""
Module 6 – Terraform Tests: Cross-Module Wiring

Same approach as Module 4/5's equivalent test file. Module 6's root
main.tf currently has TWO active module calls (s3, security_groups) --
modules/ecs and modules/iam are intentionally commented out pending
ECR/ALB/JWT-key inputs.
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


def get_required_variables(module_dir: Path) -> set[str]:
    data = merge_tf_dir(module_dir)
    required = set()
    for var_block in data.get("variable", []):
        for var_name, var_def in var_block.items():
            if "default" not in var_def:
                required.add(var_name)
    return required


def get_root_module_calls() -> Dict[str, Dict[str, Any]]:
    data = load_tf_file(TERRAFORM_ROOT / "main.tf")
    calls: Dict[str, Dict[str, Any]] = {}
    for module_block in data.get("module", []):
        for call_name, call_args in module_block.items():
            clean_args = {k: v for k, v in call_args.items() if not k.startswith("__")}
            calls[call_name] = clean_args
    return calls


ACTIVE_MODULE_CALLS = {
    "s3": "modules/s3",
    "security_groups": "modules/security_groups",
}

PENDING_MODULE_CALLS = {
    "ecs": "modules/ecs",
    "iam": "modules/iam",
}


class TestCrossModuleWiring:
    @pytest.fixture(scope="class")
    @classmethod
    def module_calls(cls) -> Dict[str, Dict[str, Any]]:
        return get_root_module_calls()

    @pytest.mark.parametrize("call_name,module_path", ACTIVE_MODULE_CALLS.items())
    def test_all_required_variables_supplied(self, module_calls, call_name, module_path):
        assert call_name in module_calls, (
            f"Expected an active 'module \"{call_name}\"' block in root main.tf, "
            f"but none was found."
        )
        required = get_required_variables(TERRAFORM_ROOT / module_path)
        supplied = set(module_calls[call_name].keys()) - {"source", "version"}
        missing = required - supplied
        assert not missing, (
            f"module \"{call_name}\" ({module_path}) is missing required "
            f"argument(s): {sorted(missing)}."
        )

    def test_no_unknown_active_modules(self, module_calls):
        unexpected = set(module_calls.keys()) - set(ACTIVE_MODULE_CALLS.keys())
        assert not unexpected, (
            f"Found module call(s) in root main.tf not covered by "
            f"ACTIVE_MODULE_CALLS: {sorted(unexpected)}."
        )


class TestPendingModulesAreReadyToUncomment:
    @pytest.mark.parametrize("module_name,module_path", PENDING_MODULE_CALLS.items())
    def test_pending_module_parses_cleanly(self, module_name, module_path):
        data = merge_tf_dir(TERRAFORM_ROOT / module_path)
        assert "variable" in data or "resource" in data

    def test_ecs_module_declares_locked_sizing_defaults(self):
        """
        Regression guard: Module 6's ECS task is intentionally sized
        smaller than Module 4/5 (no ML model to load), 0.5 vCPU / 1GB.
        """
        data = merge_tf_dir(TERRAFORM_ROOT / "modules/ecs")
        var_defaults = {
            name: var_def.get("default", [None])[0]
            for var_block in data.get("variable", [])
            for name, var_def in var_block.items()
        }
        assert var_defaults.get("task_cpu") == 512
        assert var_defaults.get("task_memory") == 1024
