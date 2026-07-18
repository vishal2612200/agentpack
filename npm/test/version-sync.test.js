"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const packageJson = require("../package.json");
const packageLock = require("../package-lock.json");
const launcher = require("../bin/agentpack.js");

const repoRoot = path.join(__dirname, "..", "..");
const pyproject = fs.readFileSync(path.join(repoRoot, "pyproject.toml"), "utf8");
const initPy = fs.readFileSync(path.join(repoRoot, "src", "agentpack", "__init__.py"), "utf8");
const codexPlugin = require(path.join(repoRoot, ".codex-plugin", "plugin.json"));
const packagedCodexPlugin = require(path.join(
  repoRoot,
  "src",
  "agentpack",
  "data",
  "codex_plugin",
  ".codex-plugin",
  "plugin.json"
));
const packagedCodexPluginPackage = require(path.join(
  repoRoot,
  "src",
  "agentpack",
  "data",
  "codex_plugin",
  "package.json"
));
const packagedCodexPluginPackageLock = require(path.join(
  repoRoot,
  "src",
  "agentpack",
  "data",
  "codex_plugin",
  "package-lock.json"
));

const pyprojectVersion = pyproject.match(/^version = "([^"]+)"/m)?.[1];
const initVersion = initPy.match(/__version__ = "([^"]+)"/)?.[1];

assert.equal(packageJson.version, pyprojectVersion, "npm package version must match pyproject.toml");
assert.equal(packageLock.version, packageJson.version, "npm package-lock version must match package.json");
assert.equal(
  packageLock.packages[""].version,
  packageJson.version,
  "npm package-lock root package must match package.json"
);
assert.equal(packageJson.version, initVersion, "npm package version must match src/agentpack/__init__.py");
assert.equal(codexPlugin.version, packageJson.version, "Codex plugin version must match npm package version");
assert.equal(packagedCodexPlugin.version, packageJson.version, "packaged Codex plugin version must match npm package version");
assert.equal(
  packagedCodexPluginPackage.version,
  packageJson.version,
  "packaged Codex plugin package version must match npm package version"
);
assert.equal(
  packagedCodexPluginPackageLock.version,
  packageJson.version,
  "packaged Codex plugin package-lock version must match npm package version"
);
assert.equal(
  packagedCodexPluginPackageLock.packages[""].version,
  packageJson.version,
  "packaged Codex plugin package-lock root package must match npm package version"
);
assert.equal(launcher.PACKAGE_VERSION, packageJson.version, "launcher version must match npm package version");
assert.equal(launcher.PYPI_PACKAGE, `agentpack-cli==${packageJson.version}`);
