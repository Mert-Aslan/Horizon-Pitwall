# Releasing

Releases are built by GitHub Actions from a version tag and published by hand. The workflow
never publishes a release and never creates tags.

## Workflows

| Workflow | Trigger | Does |
|---|---|---|
| `.github/workflows/ci.yml` | push to `main`, pull requests, manual | UI typecheck/tests/build, pytest, package build, packaged-app test, service install test on the disposable runner; uploads `dist-release/` as a 14-day artifact |
| `.github/workflows/release.yml` | tag `vX.Y.Z` pushed | same checks with the UI rebuilt from the lockfile and the tag checked against the version, then a **draft** release with the ZIP, `SHA256SUMS.txt` and the manifest |
| `release.yml`, manual run, `build-only` | Actions tab | build and tests, artifact only |
| `release.yml`, manual run, `draft` + existing tag | Actions tab | draft release for a tag that already exists |

All actions are pinned to full commit SHAs (with the version in a comment). The default
token is read-only; only the draft job gets `contents: write`. Inputs reach scripts through
environment variables, not inline expressions. An existing release for the tag is never
modified.

To update a pinned action, look up the commit of the new tag and replace the SHA:

```bash
gh api repos/actions/checkout/commits/v7.0.1 --jq .sha
```

## Steps

1. Make sure `main` is green in CI.
2. Set the version in `backend/config.py` (`APP_VERSION`) and in `frontend/package.json`
   (`npm version X.Y.Z --no-git-tag-version` in `frontend/`), run `npm run build`, update
   `README.md` / `docs/IMPLEMENTATION_STATUS.md` where they mention the version, commit.
3. Build and test locally (optional but recommended):
   ```powershell
   .\.venv-build\Scripts\python tools\build_exe.py --build-ui --tag vX.Y.Z
   .\.venv-build\Scripts\python tools\test_exe.py
   ```
4. Tag and push the tag:
   ```bash
   git tag -a vX.Y.Z -m "Horizon Pitwall X.Y.Z"
   git push origin vX.Y.Z
   ```
5. Wait for the Release workflow. Open the draft on the Releases page and check:
   - the ZIP name and size, and that `SHA256SUMS.txt` matches a local `Get-FileHash`;
   - download, extract, start, and see the dashboard with the expected version in
     Diagnostics › Server build (commit equals the tag's commit);
   - the release notes.
6. Publish the draft.

If the workflow fails, fix the problem on `main`, delete the tag locally and on GitHub
(`git push origin :refs/tags/vX.Y.Z`) only if no release was published from it, and tag
again.

## What is in a release

- `HorizonPitwall-X.Y.Z-windows-x64.zip`: one folder `HorizonPitwall-X.Y.Z/` with
  `HorizonPitwall.exe`, `HorizonPitwallService.exe`, `_internal/`, `README.txt`,
  `LICENSE.txt`, `THIRD_PARTY_NOTICES.txt`, `pitwall.config.example.json`
- `HorizonPitwall-X.Y.Z-release-manifest.json`: build metadata and every file's SHA-256
- `SHA256SUMS.txt`: checksums of the two files above

The executables are not code-signed. Signing would need a certificate and a secret in the
repository; until then the release notes explain the SmartScreen prompt and the checksums.
