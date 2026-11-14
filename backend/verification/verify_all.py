"""Run local synthetic verification and report pass, failure and missing checks.

Exit 0 only if every selected check passes, 1 on failure, 2 when checks are skipped.
External services and actual user data are never used.
"""
import importlib.util
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # Embedded Python does not add the script directory.
from test_environment import isolated_environment

# A crashed child can emit bytes that are not valid in the console code page
# (GBK on this host); never let reporting itself turn into a failure.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass
CHECKS = [
    ("storage_policy", "verify_storage_policy.py", ()),
    ("storage_contract", "verify_storage_contract.py", ()),
    ("storage_transactions", "verify_storage_transactions.py", ()),
    ("knowledge", "verify_knowledge.py", ()),
    ("knowledge_concurrency", "verify_knowledge_concurrency.py", ()),
    ("knowledge_tags", "verify_knowledge_tags.py", ()),
    ("knowledge_query", "verify_knowledge_query.py", ()),
    ("mail_todo", "verify_mail_todo.py", ()),
    ("mail", "verify_jarvis_mail.py", ()),
    ("mail_snapshot_retention", "verify_mail_snapshot_retention.py", ()),
    ("model_consent", "verify_model_consent.py", ()),
    ("reply_draft", "verify_reply_draft.py", ()),
    ("overview", "verify_overview.py", ()),
    ("overview_scale", "verify_overview_scale.py", ()),
    ("migrations", "verify_migrations.py", ()),
    ("real_synthetic_upgrade", "verify_real_upgrade.py", ()),
    ("synthetic_export", "verify_synthetic_export.py", ()),
    ("synthetic_sync", "verify_synthetic_sync.py", ()),
    ("synthetic_gmail_parser", "verify_synthetic_gmail_parser.py", ()),
    ("mail_rules", "verify_mail_rules.py", ()),
    ("task_search", "verify_task_search.py", ()),
    ("task_search_properties", "verify_search_properties.py", ()),
    ("unified_search", "verify_unified_search.py", ()),
    ("mail_threads", "verify_mail_threads.py", ()),
    ("synthetic_mail_query", "verify_mail_query.py", ()),
    ("synthetic_mail_queues", "verify_mail_queues.py", ()),
    ("listing_properties", "verify_listing_properties.py", ()),
    ("todo_concurrency", "verify_todo_concurrency.py", ()),
    ("todo_shares", "verify_todo_shares.py", ()),
    ("todo_idempotency", "verify_todo_idempotency.py", ()),
    ("synthetic_reminders", "verify_reminders.py", ()),
    ("demo_cleanup", "verify_demo.py", ()),
    ("core_services", "verify_core.py", ()),
    ("request_guard_functions", "verify_request_guard.py", ()),
    ("body_limit", "verify_body_limit.py", ()),
    ("api_input_functions", "verify_api_inputs.py", ("pydantic",)),
    ("http_runtime", "verify_http.py", ("fastapi", "pydantic", "uvicorn")),
    ("http_restart", "verify_http_restart.py", ("fastapi", "pydantic", "uvicorn")),
    ("sqlcipher_plain_driver_rejection", "verify_sqlcipher_probe_guard.py", ()),
    ("apswmc_isolated_candidate", "verify_apswmc_candidate.py", ()),
    ("apswmc_schema_experiment", "verify_apswmc_schema.py", ()),
    ("apswmc_synthetic_vault", "verify_apswmc_vault.py", ()),
    ("apswmc_backup_probe", "verify_apswmc_backup_probe.py", ()),
    ("apswmc_crash_probe", "verify_apswmc_crash_probe.py", ()),
    ("apswmc_adapter", "verify_apswmc_adapter.py", ()),
    ("apswmc_services", "verify_apswmc_services.py", ()),
    ("vault_key_lifecycle", "verify_vault_key_lifecycle.py", ()),
    ("vault_backup_restore", "verify_vault_backup_restore.py", ()),
    ("vault_rotation", "verify_vault_rotation.py", ()),
    ("vault_concurrency", "verify_vault_concurrency.py", ()),
    ("vault_backup_job", "verify_backup_job.py", ()),
    ("vault_app_schema", "verify_vault_app_schema.py", ()),
    ("dpapi_app_storage", "verify_dpapi_app_storage.py", ()),
    ("migrate_to_vault", "verify_migrate_to_vault.py", ()),
    ("driver_provenance", "verify_driver_provenance.py", ()),
    ("g1_release_gate", "verify_g1_release_gate.py", ()),
    ("env_lock", "verify_env_lock.py", ()),
    ("m1_dataset", "verify_m1_dataset.py", ()),
    ("m1_pipeline", "verify_m1_pipeline.py", ()),
    ("m1_evaluation", "verify_m1_evaluation.py", ()),
    ("m1_model_probe", "verify_m1_model_probe.py", ()),
    ("sqlcipher_candidate", "verify_sqlcipher_candidate.py", ("sqlcipher3",)),
    ("synthetic_vault", "verify_synthetic_vault.py", ("sqlcipher3",)),
    ("windows_dpapi_candidate", "verify_windows_dpapi_candidate.py", ()),
]

DIAGNOSTICS = {"driver_provenance"}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--experimental-apswmc", action="store_true", help="explicitly run compatible checks with a fresh synthetic APSW key")
options = parser.parse_args()
child_environment = isolated_environment(os.environ, experimental_apswmc=options.experimental_apswmc)
print("VERIFICATION_STORAGE=%s (inherited JARVIS_* removed)" % ("experimental-apswmc" if options.experimental_apswmc else "test-default"), flush=True)
results = {group: {"PASS": [], "FAIL": [], "SKIP": []} for group in ("functional", "diagnostic")}
for name, filename, dependencies in CHECKS:
    group = "diagnostic" if name in DIAGNOSTICS else "functional"
    group_results = results[group]
    missing = [module for module in dependencies if importlib.util.find_spec(module) is None]
    if missing:
        group_results["SKIP"].append(name)
        print(f"SKIP [{group}] {name}: missing {', '.join(missing)}", flush=True)
        continue
    try:
        run = subprocess.run([sys.executable, str(HERE / filename)], cwd=ROOT, env=child_environment,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    except subprocess.TimeoutExpired:
        group_results["FAIL"].append(name)
        print(f"FAIL [{group}] {name}: timeout", flush=True)
        continue
    if run.returncode == 0:
        group_results["PASS"].append(name)
        print(f"PASS [{group}] {name}", flush=True)
        if group == "diagnostic":
            for line in run.stdout.splitlines():
                if line.startswith(("G1_GATE=", "REAL_DATA_APPROVAL=")):
                    print(f"  {line}", flush=True)
    elif run.returncode == 2 and "NOT_RUN" in run.stdout:
        group_results["SKIP"].append(name)
        print(f"SKIP [{group}] {name}: {run.stdout.strip()}", flush=True)
    else:
        group_results["FAIL"].append(name)
        print(f"FAIL [{group}] {name} (exit {run.returncode}):\n{run.stdout}{run.stderr}", flush=True)
for group, outcome in results.items():
    print(f"{group.upper()} Summary:", ", ".join(f"{key}={len(value)}" for key, value in outcome.items()))
print("Diagnostic PASS means the check ran successfully, not that G1 is approved.")
print("All checks use synthetic data; integrated encryption, recovery and security review remain NOT VERIFIED.")
if any(group["FAIL"] for group in results.values()):
    raise SystemExit(1)
if any(group["SKIP"] for group in results.values()):
    raise SystemExit(2)
print("ALL_SELECTED_CHECKS=PASS")
