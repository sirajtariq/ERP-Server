import os
import subprocess
import sys
import shutil
import time
import argparse
import json
import re

def run_cmd(cmd, cwd=None, env=None):
    print(f"\n========================================")
    print(f"Running: {cmd}")
    if cwd:
        print(f"Working Directory: {cwd}")
    print(f"========================================")
    
    # Merge existing environment with any custom env vars passed
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
        
    result = subprocess.run(cmd, shell=True, cwd=cwd, env=run_env)
    if result.returncode != 0:
        print(f"Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)

def clean_dir(path):
    if os.path.exists(path):
        print(f"Cleaning directory: {path}...")
        for attempt in range(5):
            try:
                shutil.rmtree(path)
                print(f"Successfully cleaned {path}")
                return
            except Exception as e:
                print(f"Attempt {attempt+1}: Could not remove {path} ({e}). Retrying in 2 seconds...")
                subprocess.run("taskkill /F /IM electron.exe /IM LenDen.exe /IM ERP_Backend.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)

def update_version(client_dir, server_dir, new_version):
    print(f"\n--- Updating Version to {new_version} ---")
    
    # Update package.json
    package_json_path = os.path.join(client_dir, "package.json")
    if os.path.exists(package_json_path):
        with open(package_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data['version'] = new_version
        with open(package_json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        print(f"Updated {package_json_path}")
    
    # Update pyproject.toml
    pyproject_path = os.path.join(server_dir, "pyproject.toml")
    if os.path.exists(pyproject_path):
        with open(pyproject_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Regex to replace version = "..."
        content = re.sub(r'version\s*=\s*"[^"]+"', f'version = "{new_version}"', content, count=1)
        
        with open(pyproject_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {pyproject_path}")

def main():
    parser = argparse.ArgumentParser(description="Build Desktop App (PyInstaller + Electron)")
    parser.add_argument("--version", type=str, help="Set a new version for the build (e.g. 1.0.1)")
    parser.add_argument("--skip-backend", action="store_true", help="Skip the backend (PyInstaller) build step")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip the frontend (Electron) build step")
    args = parser.parse_args()

    root_dir = os.path.abspath(os.path.dirname(__file__))
    server_dir = os.path.join(root_dir, "server")
    client_dir = os.path.join(root_dir, "client")

    print("Starting Desktop App Build Process...")
    
    if args.version:
        update_version(client_dir, server_dir, args.version)

    # CRITICAL FIX for PyInstaller + Django:
    # Set the DJANGO_SETTINGS_MODULE environment variable so hooks don't crash
    os.environ["DJANGO_SETTINGS_MODULE"] = "erp_backend.settings"

    if not args.skip_backend:
        # Step 1: Build the backend with PyInstaller
        print("\n--- [Step 1] Building Backend ---")
        
        # Remove old database and spec file if they exist
        db_path = os.path.join(server_dir, 'db.sqlite3')
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
                print(f"Removed old database at {db_path}")
            except Exception as e:
                print(f"Warning: Could not remove old database ({e})")

        spec_path = os.path.join(server_dir, 'ERP_Backend.spec')
        if os.path.exists(spec_path):
            try:
                os.remove(spec_path)
                print(f"Removed old spec file at {spec_path}")
            except Exception as e:
                print(f"Warning: Could not remove old spec file ({e})")

        # Clean server/dist before starting backend build
        server_dist_dir = os.path.join(server_dir, 'dist')
        clean_dir(server_dist_dir)
        
        # We use uv run python -m PyInstaller to avoid script path canonicalization issues on Windows
        pyinstaller_cmd = "uv run python -m PyInstaller --name ERP_Backend --onedir --noconfirm --paths . --collect-all erp_backend --collect-all sales --collect-all purchase --collect-all dashboard --collect-all inventory --collect-all employees --collect-all waitress --copy-metadata drf_yasg --collect-all drf_yasg --collect-all rest_framework --collect-all rest_framework_simplejwt run_server.py"
        run_cmd(pyinstaller_cmd, cwd=server_dir)

        # Post-PyInstaller Cleanup: Purge db.sqlite3 from dist before Electron packaging
        dist_db_path = os.path.join(server_dir, 'dist', 'ERP_Backend', 'db.sqlite3')
        if os.path.exists(dist_db_path):
            try:
                os.remove(dist_db_path)
            except Exception as e:
                print(f"Warning: Could not remove dist database ({e})")
        print("Successfully purged db.sqlite3 from dist before Electron packaging.")

    if not args.skip_frontend:
        # Step 2: Build the frontend with Electron Builder
        print("\n--- [Step 2] Building Frontend ---")
        
        # Extend download timeouts to 1 hour and set binary mirror fallbacks
        os.environ["ELECTRON_GET_TIMEOUT"] = "3600000"
        os.environ["ELECTRON_BUILD_TIMEOUT"] = "3600000"
        os.environ["ELECTRON_BUILDER_BINARIES_MIRROR"] = "https://npmmirror.com/mirrors/electron-builder-binaries/"
        os.environ["ELECTRON_MIRROR"] = "https://npmmirror.com/mirrors/electron/"
        
        print("Download timeouts extended to 1 hour and binary mirror fallbacks configured.")
        
        release_dir = os.path.join(client_dir, "release")
        clean_dir(release_dir)
        
        # Install node modules if necessary
        run_cmd("npm install", cwd=client_dir)
        
        # Run electron builder
        run_cmd("npm run electron:build", cwd=client_dir)

    print("\n========================================")
    print("Build Complete! The final installer should be in:")
    print(os.path.join(client_dir, "release"))
    print("========================================")

if __name__ == "__main__":
    main()
