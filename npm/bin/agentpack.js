#!/usr/bin/env node
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const PACKAGE_VERSION = "0.3.38";
const PYPI_PACKAGE = `agentpack-cli==${PACKAGE_VERSION}`;

function compareVersions(left, right) {
  const a = String(left).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const b = String(right).split(".").map((part) => Number.parseInt(part, 10) || 0);
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const delta = (a[i] || 0) - (b[i] || 0);
    if (delta !== 0) {
      return delta;
    }
  }
  return 0;
}

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    encoding: "utf8",
    ...options,
  });
}

function fail(message, code = 1) {
  console.error(`agentpack npm wrapper: ${message}`);
  process.exit(code);
}

function pythonVersion(python) {
  const result = run(python.command, [
    ...python.args,
    "-c",
    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
  ]);
  if (result.status !== 0) {
    return null;
  }
  return result.stdout.trim();
}

function findPython() {
  const candidates = [
    process.env.AGENTPACK_PYTHON ? { command: process.env.AGENTPACK_PYTHON, args: [] } : null,
    process.platform === "win32" ? { command: "py", args: ["-3"] } : null,
    { command: "python3", args: [] },
    { command: "python", args: [] },
  ].filter(Boolean);

  for (const candidate of candidates) {
    const version = pythonVersion(candidate);
    if (version && compareVersions(version, "3.10") >= 0) {
      return { ...candidate, version };
    }
  }
  return null;
}

function cacheRoot() {
  if (process.env.AGENTPACK_NPM_CACHE_DIR) {
    return process.env.AGENTPACK_NPM_CACHE_DIR;
  }
  const base = process.platform === "win32"
    ? (process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"))
    : (process.env.XDG_CACHE_HOME || path.join(os.homedir(), ".cache"));
  return path.join(base, "agentpack-npm", PACKAGE_VERSION);
}

function venvPaths(root) {
  const venv = path.join(root, "venv");
  const bin = process.platform === "win32" ? path.join(venv, "Scripts") : path.join(venv, "bin");
  return {
    venv,
    python: process.platform === "win32" ? path.join(bin, "python.exe") : path.join(bin, "python"),
    agentpack: process.platform === "win32" ? path.join(bin, "agentpack.exe") : path.join(bin, "agentpack"),
    marker: path.join(root, "agentpack-cli-version.txt"),
  };
}

function installOrUpdateVenv(systemPython, paths) {
  const marker = fs.existsSync(paths.marker) ? fs.readFileSync(paths.marker, "utf8").trim() : "";
  if (marker === PACKAGE_VERSION && fs.existsSync(paths.agentpack)) {
    return;
  }

  fs.mkdirSync(path.dirname(paths.marker), { recursive: true });

  let result = run(systemPython.command, [...systemPython.args, "-m", "venv", paths.venv], { stdio: "inherit" });
  if (result.status !== 0) {
    fail(`failed to create Python virtual environment at ${paths.venv}`);
  }

  result = run(paths.python, ["-m", "pip", "install", "--upgrade", "pip"], { stdio: "inherit" });
  if (result.status !== 0) {
    fail("failed to upgrade pip in the AgentPack npm wrapper environment");
  }

  result = run(paths.python, ["-m", "pip", "install", "--upgrade", PYPI_PACKAGE], { stdio: "inherit" });
  if (result.status !== 0) {
    fail(`failed to install ${PYPI_PACKAGE}`);
  }

  fs.writeFileSync(paths.marker, `${PACKAGE_VERSION}\n`);
}

function main(
  argv = process.argv.slice(2),
  {
    cacheRootFn = cacheRoot,
    venvPathsFn = venvPaths,
    findPythonFn = findPython,
    installOrUpdateVenvFn = installOrUpdateVenv,
    spawnSyncFn = spawnSync,
  } = {},
) {
  const root = cacheRootFn();
  const paths = venvPathsFn(root);

  if (process.env.AGENTPACK_NPM_DRY_RUN === "1") {
    console.log(JSON.stringify({
      packageVersion: PACKAGE_VERSION,
      pypiPackage: PYPI_PACKAGE,
      cacheRoot: root,
      venv: paths.venv,
    }));
    return 0;
  }

  const python = findPythonFn();
  if (!python) {
    fail("Python >=3.10 is required. Install Python, set AGENTPACK_PYTHON=/path/to/python, or use the Windows py launcher.");
  }

  installOrUpdateVenvFn(python, paths);

  const result = spawnSyncFn(paths.agentpack, argv, {
    stdio: "inherit",
    env: process.env,
  });
  if (result.error) {
    fail(`failed to run ${paths.agentpack}: ${result.error.message}`);
  }
  return typeof result.status === "number" ? result.status : 1;
}

if (require.main === module) {
  process.exit(main());
}

module.exports = {
  PACKAGE_VERSION,
  PYPI_PACKAGE,
  cacheRoot,
  compareVersions,
  findPython,
  installOrUpdateVenv,
  main,
  pythonVersion,
  venvPaths,
};
