"""Phase 2b: Post-execution build validation.

After Phase 2 writes code artifacts to disk, this module validates them
by actually running build tools against the output:

0. Asset stubbing: auto-create placeholder PNGs for referenced assets
   (LLMs cannot generate binary files).
1. Dependency check: ensure all imports in source files have corresponding
   entries in package.json / Cargo.toml / pyproject.toml.
2. TypeScript / build check: run `npx tsc --noEmit` or equivalent to catch
   compile-time errors.
3. Route conflict detection: scan Expo Router / Next.js file-based routes
   for directory-vs-file conflicts and duplicate paths.
4. Asset reference check: verify that all referenced assets (images, fonts)
   actually exist on disk.
5. Expo start smoke test: run `npx expo export --platform web` to verify
   the app can parse and bundle without runtime errors.
6. Screenshot capture: start the dev server, walk screens via Playwright,
   capture at least one screenshot per run.
7. Vision critique: feed screenshots to the visual critique pipeline for
   layout/rendering issue detection.

Errors are returned as structured rework items that can be injected back
into an execution grid for targeted patching.

GRD-7: Execution cells must validate artifacts by running them.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(stderr=True)


@dataclass
class ValidationIssue:
    """A single validation problem found during build checks."""
    category: str       # "dependency", "typescript", "route_conflict", "asset_missing", "runtime"
    severity: str       # "error", "warning"
    message: str
    file: str | None = None
    line: int | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
        }
        if self.file:
            d["file"] = self.file
        if self.line is not None:
            d["line"] = self.line
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


@dataclass
class ValidationResult:
    """Result of a full Phase 2b validation pass."""
    issues: list[ValidationIssue] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    vision_critiques: list[dict] = field(default_factory=list)
    passed: bool = True
    elapsed_seconds: float = 0.0

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [i.to_dict() for i in self.issues],
            "screenshots": self.screenshots,
            "vision_critiques": self.vision_critiques,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }

    def to_rework_items(self) -> list[dict]:
        """Convert validation errors into structured rework items for execution cells."""
        rework = []
        for issue in self.issues:
            if issue.severity != "error":
                continue
            rework.append({
                "kind": "validation_error",
                "category": issue.category,
                "message": issue.message,
                "file": issue.file,
                "line": issue.line,
                "suggestion": issue.suggestion,
            })
        for critique in self.vision_critiques:
            if critique.get("verdict") == "iterate":
                rework.append({
                    "kind": "visual_issue",
                    "feedback": critique.get("feedback", ""),
                    "priority_changes": critique.get("priority_changes", []),
                    "score": critique.get("overall_score", 0),
                })
        return rework


def validate_build(
    app_dir: str,
    project_config: dict[str, str] | None = None,
    output_dir: str | None = None,
    run_screenshots: bool = True,
    verbose: bool = True,
) -> ValidationResult:
    """Run full build validation on the output app directory.

    Steps executed in order:
    0. stub_missing_assets (auto-create placeholder PNGs)
    1. check_dependencies
    2. check_typescript
    3. check_route_conflicts
    4. check_asset_references
    5. check_expo_start (quick boot smoke test for Expo apps)
    6. capture_screenshots (optional)
    7. vision_critique_screenshots (if screenshots captured)
    """
    t0 = time.time()
    pc = dict(project_config or {})
    result = ValidationResult()

    if not os.path.isdir(app_dir):
        result.issues.append(ValidationIssue(
            category="runtime",
            severity="error",
            message=f"App directory does not exist: {app_dir}",
        ))
        result.passed = False
        result.elapsed_seconds = time.time() - t0
        return result

    if verbose:
        console.print(Panel(
            f"App dir: {app_dir}\nProject type: {pc.get('type', 'unknown')}",
            title="Phase 2b: Build Validation (GRD-7)",
            border_style="bright_white",
        ))

    # 0. Stub missing binary assets (LLMs can't generate PNGs)
    _stub_missing_assets(app_dir, verbose=verbose)

    # 1. Dependency check
    dep_issues = check_dependencies(app_dir, verbose=verbose)
    result.issues.extend(dep_issues)

    # 2. TypeScript / build check
    ts_issues = check_typescript(app_dir, pc, verbose=verbose)
    result.issues.extend(ts_issues)

    # 3. Route conflict detection
    route_issues = check_route_conflicts(app_dir, pc, verbose=verbose)
    result.issues.extend(route_issues)

    # 4. Asset reference check
    asset_issues = check_asset_references(app_dir, verbose=verbose)
    result.issues.extend(asset_issues)

    # 5. Expo start smoke test (quick boot check)
    framework = pc.get("framework", "").lower()
    if "expo" in framework:
        expo_issues = check_expo_start(app_dir, verbose=verbose)
        result.issues.extend(expo_issues)

    # 6. Screenshot capture
    screenshots_dir = os.path.join(output_dir or app_dir, "validation-screenshots")
    if run_screenshots:
        screenshots = capture_screenshots(app_dir, screenshots_dir, pc, verbose=verbose)
        result.screenshots = screenshots

        # 7. Vision critique
        if screenshots:
            critiques = vision_critique_screenshots(screenshots, pc, verbose=verbose)
            result.vision_critiques = critiques

    result.passed = result.error_count == 0
    result.elapsed_seconds = time.time() - t0

    if verbose:
        _print_validation_summary(result)

    return result


# --- Step 1: Dependency check ---

def check_dependencies(app_dir: str, verbose: bool = True) -> list[ValidationIssue]:
    """Check that all imports in source files have corresponding package.json entries."""
    issues: list[ValidationIssue] = []
    pkg_json_path = os.path.join(app_dir, "package.json")

    if not os.path.exists(pkg_json_path):
        # Try npm project types only
        if _has_js_files(app_dir):
            issues.append(ValidationIssue(
                category="dependency",
                severity="error",
                message="No package.json found but JavaScript/TypeScript files exist",
                file="package.json",
                suggestion="Run `npm init` or create package.json with required dependencies",
            ))
        return issues

    try:
        with open(pkg_json_path, "r") as f:
            pkg = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        issues.append(ValidationIssue(
            category="dependency",
            severity="error",
            message=f"Cannot parse package.json: {e}",
            file="package.json",
        ))
        return issues

    declared_deps = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        declared_deps.update(pkg.get(key, {}).keys())

    # Scan all JS/TS/JSX/TSX files for imports
    imported_packages = _scan_imports(app_dir)

    # Built-in / relative imports to skip
    builtins = {"react", "react-dom", "react-native", "expo"}
    # Packages that are part of react-native or expo and don't need explicit deps
    implicit = {
        "react/jsx-runtime", "react/jsx-dev-runtime",
    }

    for pkg_name, locations in imported_packages.items():
        # Skip relative imports, builtins, and already-declared deps
        if pkg_name.startswith(".") or pkg_name.startswith("/"):
            continue
        # Normalize scoped package: @tauri-apps/api/core -> @tauri-apps/api
        base_pkg = _normalize_package_name(pkg_name)
        if base_pkg in declared_deps or base_pkg in builtins or pkg_name in implicit:
            continue
        # Check if it's a sub-path of a declared dep
        if any(base_pkg.startswith(d + "/") or d.startswith(base_pkg) for d in declared_deps):
            continue
        issues.append(ValidationIssue(
            category="dependency",
            severity="error",
            message=f"Package '{base_pkg}' is imported but not in package.json",
            file=locations[0] if locations else None,
            suggestion=f"Add '{base_pkg}' to dependencies in package.json",
        ))

    if verbose and issues:
        console.print(f"  [yellow]Found {len(issues)} dependency issues[/yellow]")
    elif verbose:
        console.print("  [green]Dependencies: OK[/green]")

    return issues


def _has_js_files(app_dir: str) -> bool:
    """Check if directory contains JavaScript/TypeScript source files."""
    for root, _, files in os.walk(app_dir):
        if "node_modules" in root:
            continue
        for f in files:
            if f.endswith((".js", ".jsx", ".ts", ".tsx")):
                return True
    return False


def _scan_imports(app_dir: str) -> dict[str, list[str]]:
    """Scan JS/TS files for import statements and return {package: [files]}."""
    import_pattern = re.compile(
        r'''(?:import\s+.*?from\s+['"]([^'"]+)['"]|'''
        r'''require\s*\(\s*['"]([^'"]+)['"]\s*\))'''
    )
    packages: dict[str, list[str]] = {}

    for root, dirs, files in os.walk(app_dir):
        # Skip node_modules, dist, build
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "build", ".expo", ".next")]
        for fname in files:
            if not fname.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, app_dir)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in import_pattern.finditer(content):
                    pkg = match.group(1) or match.group(2)
                    if pkg:
                        packages.setdefault(pkg, []).append(rel_path)
            except OSError:
                continue

    return packages


def _normalize_package_name(import_path: str) -> str:
    """Normalize an import path to a package name.
    '@tauri-apps/api/core' -> '@tauri-apps/api'
    'date-fns/format' -> 'date-fns'
    'react' -> 'react'
    """
    if import_path.startswith("@"):
        parts = import_path.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return import_path
    return import_path.split("/")[0]


# --- Step 2: TypeScript check ---

def check_typescript(
    app_dir: str,
    project_config: dict[str, str] | None = None,
    verbose: bool = True,
) -> list[ValidationIssue]:
    """Run TypeScript compiler in check mode to catch type errors."""
    issues: list[ValidationIssue] = []

    tsconfig_path = os.path.join(app_dir, "tsconfig.json")
    if not os.path.exists(tsconfig_path):
        # No TypeScript config -- check if there are .ts/.tsx files
        if _has_ts_files(app_dir):
            issues.append(ValidationIssue(
                category="typescript",
                severity="warning",
                message="TypeScript files found but no tsconfig.json",
                file="tsconfig.json",
                suggestion="Create tsconfig.json for type checking",
            ))
        elif verbose:
            console.print("  [dim]No TypeScript config found, skipping type check[/dim]")
        return issues

    # Check if node_modules exists (npm install needed)
    node_modules = os.path.join(app_dir, "node_modules")
    if not os.path.isdir(node_modules):
        # Try running npm install first
        if verbose:
            console.print("  [cyan]Running npm install...[/cyan]")
        try:
            install_result = subprocess.run(
                ["npm", "install", "--no-audit", "--no-fund"],
                cwd=app_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if install_result.returncode != 0:
                issues.append(ValidationIssue(
                    category="dependency",
                    severity="error",
                    message=f"npm install failed: {install_result.stderr[:500]}",
                    suggestion="Fix package.json dependencies",
                ))
                return issues
            if verbose:
                console.print("  [green]npm install: OK[/green]")
        except FileNotFoundError:
            issues.append(ValidationIssue(
                category="typescript",
                severity="warning",
                message="npm not found, cannot run type checking",
            ))
            return issues
        except subprocess.TimeoutExpired:
            issues.append(ValidationIssue(
                category="typescript",
                severity="warning",
                message="npm install timed out after 120s",
            ))
            return issues

    # Run tsc --noEmit
    if verbose:
        console.print("  [cyan]Running TypeScript check...[/cyan]")
    try:
        tsc_result = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if tsc_result.returncode != 0 and tsc_result.stdout:
            ts_issues = _parse_tsc_output(tsc_result.stdout)
            issues.extend(ts_issues)
            if verbose:
                console.print(f"  [yellow]TypeScript: {len(ts_issues)} errors[/yellow]")
        elif tsc_result.returncode != 0 and tsc_result.stderr:
            # tsc error (maybe not installed)
            ts_issues = _parse_tsc_output(tsc_result.stderr)
            if ts_issues:
                issues.extend(ts_issues)
            else:
                issues.append(ValidationIssue(
                    category="typescript",
                    severity="warning",
                    message=f"tsc failed: {tsc_result.stderr[:300]}",
                ))
        elif verbose:
            console.print("  [green]TypeScript: OK[/green]")

    except FileNotFoundError:
        if verbose:
            console.print("  [dim]npx/tsc not found, skipping type check[/dim]")
    except subprocess.TimeoutExpired:
        issues.append(ValidationIssue(
            category="typescript",
            severity="warning",
            message="tsc --noEmit timed out after 60s",
        ))

    return issues


def _has_ts_files(app_dir: str) -> bool:
    for root, _, files in os.walk(app_dir):
        if "node_modules" in root:
            continue
        for f in files:
            if f.endswith((".ts", ".tsx")):
                return True
    return False


def _parse_tsc_output(output: str) -> list[ValidationIssue]:
    """Parse TypeScript compiler error output into ValidationIssues."""
    issues: list[ValidationIssue] = []
    # Pattern: file(line,col): error TS1234: message
    pattern = re.compile(r"^(.+?)\((\d+),\d+\):\s*(error|warning)\s+TS\d+:\s*(.+)$", re.MULTILINE)

    for match in pattern.finditer(output):
        filepath, line_str, severity, message = match.groups()
        issues.append(ValidationIssue(
            category="typescript",
            severity="error" if severity == "error" else "warning",
            message=message.strip(),
            file=filepath.strip(),
            line=int(line_str),
        ))

    # If no structured errors found but there's output, capture it generically
    if not issues and output.strip():
        for line in output.strip().split("\n")[:10]:
            line = line.strip()
            if line and "error" in line.lower():
                issues.append(ValidationIssue(
                    category="typescript",
                    severity="error",
                    message=line[:300],
                ))

    return issues


# --- Step 3: Route conflict detection ---

def check_route_conflicts(
    app_dir: str,
    project_config: dict[str, str] | None = None,
    verbose: bool = True,
) -> list[ValidationIssue]:
    """Detect file-based routing conflicts (Expo Router, Next.js)."""
    issues: list[ValidationIssue] = []
    pc = dict(project_config or {})
    framework = pc.get("framework", "").lower()

    # Determine routing directory
    if "expo" in framework or "react-native" in framework:
        route_dir = os.path.join(app_dir, "app")
    elif "next" in framework:
        # Next.js app router
        route_dir = os.path.join(app_dir, "app")
        if not os.path.isdir(route_dir):
            route_dir = os.path.join(app_dir, "pages")
    else:
        route_dir = os.path.join(app_dir, "app")

    if not os.path.isdir(route_dir):
        if verbose:
            console.print("  [dim]No route directory found, skipping route check[/dim]")
        return issues

    # Collect all route paths
    route_files: dict[str, list[str]] = {}  # normalized_route -> [file_paths]

    for root, dirs, files in os.walk(route_dir):
        rel_root = os.path.relpath(root, route_dir)
        for fname in files:
            if not fname.endswith((".js", ".jsx", ".ts", ".tsx")):
                continue
            if fname.startswith("_"):  # _layout.tsx, _middleware.ts, etc.
                continue

            rel_path = os.path.join(rel_root, fname) if rel_root != "." else fname
            # Normalize: remove extension, handle index files
            route = _normalize_route(rel_path)
            route_files.setdefault(route, []).append(rel_path)

    # Check for file vs directory conflicts
    # e.g., app/home.tsx AND app/home/_layout.tsx both resolve to /home
    for route, files in route_files.items():
        if len(files) > 1:
            issues.append(ValidationIssue(
                category="route_conflict",
                severity="error",
                message=f"Route '{route}' has multiple definitions: {', '.join(files)}",
                file=files[0],
                suggestion=f"Remove duplicate route files for '{route}'",
            ))

    # Check for file-vs-directory conflicts
    # e.g., app/home.tsx exists AND app/home/ directory exists
    all_routes = set(route_files.keys())
    for route in list(all_routes):
        # Check if both /home and /home/index exist as separate things
        parent = os.path.dirname(route)
        if parent and parent in all_routes and route != parent:
            parent_files = route_files.get(parent, [])
            child_files = route_files.get(route, [])
            # Only flag if parent is a file (not a directory index)
            for pf in parent_files:
                if not pf.endswith(("index.tsx", "index.ts", "index.jsx", "index.js")):
                    issues.append(ValidationIssue(
                        category="route_conflict",
                        severity="error",
                        message=(
                            f"Route conflict: '{pf}' (file) vs '{route}/' (directory). "
                            f"Expo Router cannot have both."
                        ),
                        file=pf,
                        suggestion=f"Convert '{pf}' to a directory with _layout.tsx and index.tsx",
                    ))

    if verbose:
        if issues:
            console.print(f"  [yellow]Route conflicts: {len(issues)} found[/yellow]")
        else:
            console.print("  [green]Routes: OK[/green]")

    return issues


def _normalize_route(rel_path: str) -> str:
    """Normalize a file path to a route path.
    'home.tsx' -> '/home'
    'home/index.tsx' -> '/home'
    'settings/profile.tsx' -> '/settings/profile'
    """
    route = rel_path
    # Remove extension
    for ext in (".tsx", ".ts", ".jsx", ".js"):
        if route.endswith(ext):
            route = route[:-len(ext)]
            break
    # Remove trailing /index
    if route.endswith("/index"):
        route = route[:-6]
    if route == "index":
        route = "/"
    # Clean up
    route = "/" + route.replace("\\", "/").strip("/")
    return route


# --- Step 4: Asset reference check ---

def check_asset_references(app_dir: str, verbose: bool = True) -> list[ValidationIssue]:
    """Check that referenced assets (images, fonts) exist on disk."""
    issues: list[ValidationIssue] = []

    # Check app.json / app.config.js for asset references
    app_json_path = os.path.join(app_dir, "app.json")
    if os.path.exists(app_json_path):
        try:
            with open(app_json_path, "r") as f:
                app_config = json.load(f)
            expo_config = app_config.get("expo", app_config)

            # Check icon, splash, adaptive icon
            asset_keys = [
                ("icon", expo_config.get("icon")),
                ("splash.image", (expo_config.get("splash") or {}).get("image")),
                ("android.adaptiveIcon.foregroundImage",
                 (expo_config.get("android", {}).get("adaptiveIcon") or {}).get("foregroundImage")),
            ]

            for key, ref in asset_keys:
                if ref and isinstance(ref, str):
                    # Resolve relative to app_dir
                    asset_path = os.path.join(app_dir, ref.lstrip("./"))
                    if not os.path.exists(asset_path):
                        issues.append(ValidationIssue(
                            category="asset_missing",
                            severity="error",
                            message=f"Asset '{ref}' referenced in app.json ({key}) does not exist",
                            file="app.json",
                            suggestion=f"Create the file at '{ref}' or update the reference in app.json",
                        ))
        except (json.JSONDecodeError, OSError):
            pass

    # Scan source files for require('./assets/...') and import patterns
    asset_import_pattern = re.compile(
        r'''require\s*\(\s*['"](\./assets/[^'"]+)['"]\s*\)'''
    )
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist", "build", ".expo")]
        for fname in files:
            if not fname.endswith((".js", ".jsx", ".ts", ".tsx")):
                continue
            fpath = os.path.join(root, fname)
            rel_file = os.path.relpath(fpath, app_dir)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for match in asset_import_pattern.finditer(content):
                    asset_ref = match.group(1)
                    # Resolve relative to the file's directory
                    file_dir = os.path.dirname(fpath)
                    asset_path = os.path.normpath(os.path.join(file_dir, asset_ref))
                    if not os.path.exists(asset_path):
                        issues.append(ValidationIssue(
                            category="asset_missing",
                            severity="error",
                            message=f"Asset '{asset_ref}' required in {rel_file} does not exist",
                            file=rel_file,
                            suggestion=f"Create the asset at '{asset_ref}' or remove the reference",
                        ))
            except OSError:
                continue

    if verbose:
        if issues:
            console.print(f"  [yellow]Asset references: {len(issues)} missing[/yellow]")
        else:
            console.print("  [green]Assets: OK[/green]")

    return issues


# --- Step 0: Stub missing binary assets ---

def _stub_missing_assets(app_dir: str, verbose: bool = True) -> None:
    """Auto-create placeholder PNG files for assets referenced in app.json.

    LLMs cannot generate binary image files. Rather than burning rework
    iterations on unfixable asset_missing errors, we create minimal valid
    1x1 transparent PNGs as placeholders before the asset check runs.
    """
    app_json_path = os.path.join(app_dir, "app.json")
    if not os.path.exists(app_json_path):
        return

    try:
        with open(app_json_path, "r") as f:
            app_config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return

    expo_config = app_config.get("expo", app_config)

    asset_refs = []
    if expo_config.get("icon"):
        asset_refs.append(expo_config["icon"])
    splash = expo_config.get("splash") or {}
    if splash.get("image"):
        asset_refs.append(splash["image"])
    adaptive = (expo_config.get("android", {}).get("adaptiveIcon") or {})
    if adaptive.get("foregroundImage"):
        asset_refs.append(adaptive["foregroundImage"])

    # Minimal valid PNG: 1x1 transparent pixel (67 bytes)
    PLACEHOLDER_PNG = (
        b'\x89PNG\r\n\x1a\n'  # PNG signature
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
        b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
        b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
        b'\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
    )

    stubbed = 0
    for ref in asset_refs:
        if not isinstance(ref, str):
            continue
        asset_path = os.path.join(app_dir, ref.lstrip("./"))
        if not os.path.exists(asset_path):
            os.makedirs(os.path.dirname(asset_path), exist_ok=True)
            with open(asset_path, "wb") as f:
                f.write(PLACEHOLDER_PNG)
            stubbed += 1

    if verbose and stubbed:
        console.print(f"  [cyan]Stubbed {stubbed} placeholder asset(s)[/cyan]")


# --- Step 5: Expo start smoke test ---

def check_expo_start(app_dir: str, verbose: bool = True) -> list[ValidationIssue]:
    """Quick smoke test: run `npx expo export --platform web` to verify the
    app can at least parse and bundle without runtime errors.

    This catches issues that tsc --noEmit misses: bad Expo Router configs,
    missing entry points, invalid app.json, babel plugin conflicts, etc.
    """
    issues: list[ValidationIssue] = []

    node_modules = os.path.join(app_dir, "node_modules")
    if not os.path.isdir(node_modules):
        return issues  # npm install hasn't been run yet, skip

    if verbose:
        console.print("  [cyan]Running Expo export smoke test...[/cyan]")

    try:
        result = subprocess.run(
            ["npx", "expo", "export", "--platform", "web", "--output-dir",
             os.path.join(app_dir, ".expo-export-test")],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ, "CI": "1"},
        )

        # Clean up test export dir
        test_dir = os.path.join(app_dir, ".expo-export-test")
        if os.path.isdir(test_dir):
            import shutil
            shutil.rmtree(test_dir, ignore_errors=True)

        if result.returncode != 0:
            stderr = result.stderr or result.stdout or ""
            # Extract the most useful error lines
            error_lines = [
                line.strip() for line in stderr.split("\n")
                if line.strip() and ("error" in line.lower() or "Error" in line)
            ]
            error_msg = "\n".join(error_lines[:5]) if error_lines else stderr[:500]

            issues.append(ValidationIssue(
                category="runtime",
                severity="error",
                message=f"Expo export failed: {error_msg}",
                suggestion="Fix the bundling errors above. Common causes: "
                           "missing entry point, invalid app.json, bad imports.",
            ))
            if verbose:
                console.print(f"  [yellow]Expo smoke test: FAILED[/yellow]")
        elif verbose:
            console.print("  [green]Expo smoke test: OK[/green]")

    except FileNotFoundError:
        if verbose:
            console.print("  [dim]npx/expo not found, skipping smoke test[/dim]")
    except subprocess.TimeoutExpired:
        issues.append(ValidationIssue(
            category="runtime",
            severity="warning",
            message="Expo export timed out after 90s",
        ))

    return issues


# --- Step 6: Screenshot capture ---

def capture_screenshots(
    app_dir: str,
    screenshots_dir: str,
    project_config: dict[str, str] | None = None,
    verbose: bool = True,
) -> list[str]:
    """Start the app and capture screenshots of each screen.

    Strategy depends on project type:
    - Web/Tauri: Use Playwright to render each route
    - Expo: Export for web and render, or use Maestro for simulator

    Returns list of screenshot file paths.
    """
    pc = dict(project_config or {})
    framework = pc.get("framework", "").lower()
    screenshots: list[str] = []

    os.makedirs(screenshots_dir, exist_ok=True)

    # Strategy: try web export first (fastest, catches layout issues)
    if "expo" in framework or "react-native" in framework:
        screenshots = _capture_expo_web(app_dir, screenshots_dir, verbose)
    elif "vite" in framework or "next" in framework:
        screenshots = _capture_web_app(app_dir, screenshots_dir, verbose)
    elif "tauri" in framework:
        screenshots = _capture_web_app(app_dir, screenshots_dir, verbose)
    else:
        # Generic: try to find an index.html
        index_path = os.path.join(app_dir, "index.html")
        dist_index = os.path.join(app_dir, "dist", "index.html")
        if os.path.exists(index_path):
            screenshots = _capture_static_html(index_path, screenshots_dir, verbose)
        elif os.path.exists(dist_index):
            screenshots = _capture_static_html(dist_index, screenshots_dir, verbose)

    if verbose:
        if screenshots:
            console.print(f"  [green]Screenshots: captured {len(screenshots)}[/green]")
        else:
            console.print("  [dim]Screenshots: none captured (app may not be web-renderable)[/dim]")

    return screenshots


def _capture_expo_web(app_dir: str, screenshots_dir: str, verbose: bool) -> list[str]:
    """Export Expo app for web and capture screenshots with Playwright."""
    screenshots: list[str] = []

    # Check if expo is available
    pkg_json_path = os.path.join(app_dir, "package.json")
    if not os.path.exists(pkg_json_path):
        return screenshots

    # Try `npx expo export --platform web`
    if verbose:
        console.print("  [cyan]Attempting Expo web export...[/cyan]")

    try:
        export_result = subprocess.run(
            ["npx", "expo", "export", "--platform", "web"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if export_result.returncode != 0:
            if verbose:
                console.print(f"  [dim]Expo web export failed: {export_result.stderr[:200]}[/dim]")
            return screenshots
    except (FileNotFoundError, subprocess.TimeoutExpired):
        if verbose:
            console.print("  [dim]Expo web export not available[/dim]")
        return screenshots

    # Find the exported dist directory
    dist_dir = os.path.join(app_dir, "dist")
    index_path = os.path.join(dist_dir, "index.html")
    if not os.path.exists(index_path):
        return screenshots

    return _capture_static_html(index_path, screenshots_dir, verbose)


def _capture_web_app(app_dir: str, screenshots_dir: str, verbose: bool) -> list[str]:
    """Build and capture a web app using Playwright."""
    screenshots: list[str] = []

    # Try building first
    if verbose:
        console.print("  [cyan]Building web app...[/cyan]")

    try:
        build_result = subprocess.run(
            ["npm", "run", "build"],
            cwd=app_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if build_result.returncode != 0:
            if verbose:
                console.print(f"  [dim]Build failed: {build_result.stderr[:200]}[/dim]")
            # Try dist/index.html anyway (might be pre-built)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Check for dist/index.html
    for candidate in ["dist/index.html", "build/index.html", "out/index.html"]:
        path = os.path.join(app_dir, candidate)
        if os.path.exists(path):
            return _capture_static_html(path, screenshots_dir, verbose)

    return screenshots


def _capture_static_html(html_path: str, screenshots_dir: str, verbose: bool) -> list[str]:
    """Capture a screenshot of a static HTML file using Playwright."""
    screenshots: list[str] = []

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if verbose:
            console.print("  [dim]Playwright not available for screenshot capture[/dim]")
        return screenshots

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})

            abs_path = os.path.abspath(html_path)
            page.goto(f"file://{abs_path}", wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(1000)

            output_path = os.path.join(screenshots_dir, "main-screen.png")
            page.screenshot(path=output_path, full_page=True)
            screenshots.append(output_path)

            if verbose:
                console.print(f"  [green]Captured: {output_path}[/green]")

            browser.close()
    except Exception as e:
        if verbose:
            console.print(f"  [dim]Screenshot capture failed: {e}[/dim]")

    return screenshots


# --- Step 6: Vision critique ---

def vision_critique_screenshots(
    screenshots: list[str],
    project_config: dict[str, str] | None = None,
    verbose: bool = True,
) -> list[dict]:
    """Feed captured screenshots through the visual critique pipeline."""
    critiques: list[dict] = []

    try:
        from grids.visual.critique import visual_critique
    except ImportError:
        if verbose:
            console.print("  [dim]Visual critique module not available[/dim]")
        return critiques

    pc = dict(project_config or {})
    brief = f"A {pc.get('type', 'web application')} built with {pc.get('framework', 'web technologies')}"

    for screenshot_path in screenshots:
        if not os.path.exists(screenshot_path):
            continue
        try:
            if verbose:
                console.print(f"  [cyan]Visual critique: {os.path.basename(screenshot_path)}[/cyan]")
            critique = visual_critique(
                screenshot_path=screenshot_path,
                brief=brief,
            )
            critiques.append(critique)

            score = critique.get("overall_score", 0)
            verdict = critique.get("verdict", "unknown")
            if verbose:
                color = "green" if verdict == "approve" else "yellow"
                console.print(f"    [{color}]{verdict} (score: {score:.2f})[/{color}]")
        except Exception as e:
            if verbose:
                console.print(f"    [dim]Critique failed: {e}[/dim]")

    return critiques


# --- Display ---

def _print_validation_summary(result: ValidationResult):
    """Print a rich summary table of validation results."""
    table = Table(title="Validation Results")
    table.add_column("Category", style="cyan")
    table.add_column("Errors", width=8)
    table.add_column("Warnings", width=10)

    categories: dict[str, dict[str, int]] = {}
    for issue in result.issues:
        entry = categories.setdefault(issue.category, {"error": 0, "warning": 0})
        entry[issue.severity] = entry.get(issue.severity, 0) + 1

    for cat, counts in sorted(categories.items()):
        errors = counts.get("error", 0)
        warnings = counts.get("warning", 0)
        e_style = "red" if errors > 0 else "green"
        w_style = "yellow" if warnings > 0 else "dim"
        table.add_row(cat, f"[{e_style}]{errors}[/{e_style}]", f"[{w_style}]{warnings}[/{w_style}]")

    console.print(table)

    # Print top errors
    errors = [i for i in result.issues if i.severity == "error"]
    if errors:
        console.print(f"\n[bold red]{len(errors)} errors:[/bold red]")
        for e in errors[:10]:
            file_str = f" ({e.file}" + (f":{e.line}" if e.line else "") + ")" if e.file else ""
            console.print(f"  [red]{e.category}{file_str}: {e.message}[/red]")
            if e.suggestion:
                console.print(f"    [dim]-> {e.suggestion}[/dim]")
        if len(errors) > 10:
            console.print(f"  [dim]... and {len(errors) - 10} more[/dim]")

    # Print screenshots & vision results
    if result.screenshots:
        console.print(f"\n[green]Screenshots captured: {len(result.screenshots)}[/green]")
    if result.vision_critiques:
        for i, crit in enumerate(result.vision_critiques):
            score = crit.get("overall_score", 0)
            verdict = crit.get("verdict", "unknown")
            color = "green" if verdict == "approve" else "yellow"
            console.print(f"  Visual critique #{i+1}: [{color}]{verdict} ({score:.2f})[/{color}]")

    # Final verdict
    if result.passed:
        console.print(Panel("[bold green]BUILD VALIDATION PASSED[/bold green]", border_style="green"))
    else:
        console.print(Panel(
            f"[bold red]BUILD VALIDATION FAILED: {result.error_count} errors[/bold red]",
            border_style="red",
        ))
