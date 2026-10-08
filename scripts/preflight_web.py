import os

def check_dir(path):
    return os.path.exists(path)

print("| Component | Exists |")
print("|---|---|")
print(f"| Routes (web/app) | {check_dir('web/app')} |")
print(f"| Components (web/components) | {check_dir('web/components')} |")
print(f"| API Client (web/lib/client.ts) | {check_dir('web/lib/client.ts')} |")
print(f"| OpenAPI Snapshot (docs/api/openapi.yaml) | {check_dir('docs/api/openapi.yaml')} |")
print(f"| F-rule lint | False |")
print(f"| Tests (web/tests) | {check_dir('web/tests')} |")
