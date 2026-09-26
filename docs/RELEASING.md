# Build and publish a release

## GitHub Actions

`.github/workflows/release.yml` runs on every push to `main`, including PR
merges, squash merges, rebase merges, and direct pushes. After Python/dashboard
tests and the dashboard build pass, it packages the allowlisted application and
creates a Git tag and GitHub **pre-release** automatically. No manual tagging is
required in Orca or a shell.

Tags use the package version plus the workflow run number: for example,
`v0.1.0-rc.42`. The archive is `graf-0.1.0-rc.42.tar.gz`, with a `.sha256`
checksum. The tag points to the exact tested commit, even if another merge has
since updated `main`. Candidate numbers can have gaps. A rerun reuses its number,
resumes an unfinished draft, and leaves an already published candidate or promoted
release unchanged. Upload failures leave a draft; publication happens only after
both assets upload. Each main push runs independently; newer merges do not cancel
older candidates.

### Promote a candidate using GitHub's web UI

1. Open the repository's **Releases** page and choose the tested candidate.
2. Click the pencil/edit control for that release.
3. Clear **Set as a pre-release**. Select **Set as the latest release** if desired.
4. Click **Update release**.

Promotion keeps the same tag, commit, and downloadable assets; it does not rebuild
or remove the `-rc.N` suffix. You can change the display title when promoting it.
For the next version series, update the matching base version in
`evidencekg/pyproject.toml`, `product/ui/package.json` and its lockfile, and
`plugins/graf/.codex-plugin/plugin.json`; refresh `evidencekg/uv.lock`. You do
not need a version bump for each merge. The builder rejects mismatched component
versions and tags.

Both jobs select only the exact `self-hosted` runner label. The build runner must
provide Linux with Docker job-container support and network access to the image
and package repositories, plus the standard GitHub Actions runner prerequisites.
The build runs in `node:22-bookworm-slim` with `no-new-privileges` enabled. It
installs packages as the container's root user without `sudo` or host package
changes. The build job installs Git, CA certificates, and
`poppler-utils tesseract-ocr tesseract-ocr-eng libseccomp2` before checking
Poppler (`pdftoppm`), English OCR data, and `libseccomp.so.2`.
Actions also installs Node 22, uv and
Python 3.12. Only the publishing job gets `contents: write`, using the built-in
`GITHUB_TOKEN`; no personal token is needed. Repository rules must permit this
token to create candidate tags and releases. Pull requests do not run automatically
on the private runner. Maintainers can manually run the workflow on a reviewed
branch; manual runs produce an Actions artifact but never publish a tag or release.
Tag pushes do not trigger this workflow.

The archive includes the built dashboard and Codex plugin; Python dependencies and
models are downloaded during installation. Download both assets from GitHub Releases,
then verify and extract (substitute your candidate number):

```sh
sha256sum --check graf-0.1.0-rc.42.tar.gz.sha256
tar -xzf graf-0.1.0-rc.42.tar.gz
cd graf-0.1.0-rc.42
./graf install --demo
```

Releases in a private repository are accessible only to people with repository
access. Public downloads require a public, reviewed distribution repository.

## Local acceptance and clean publication

Before committing workflow changes, run the build job with `act` and Docker:

```sh
act workflow_dispatch -W .github/workflows/release.yml -j build \
  -P self-hosted=catthehacker/ubuntu:act-24.04 \
  --env-file /dev/null --secret-file /dev/null --input-file /dev/null \
  --artifact-server-path "$(mktemp -d /tmp/graf-action-artifacts.XXXXXX)"
```

This executes tests, packaging, and a local artifact upload without publishing.
It does not verify the hosted runner's Docker access or GitHub artifact service;
those require a manual Actions run on a reviewed, pushed branch.

Do not publish an existing research workspace wholesale. Private corpora, credentials, benchmark receipts and historical artifacts may be present beside the source code. The release builder copies only named product paths into a fresh staging tree.

1. Run the checks in VERIFICATION.md, including real local ingestion, browser journeys, removal gating and an isolated backup restore.
2. Run `npm ci`, `npm test`, and `npm run build` inside product/ui.
3. From the root, run `python3 scripts/build_release.py --output /absolute/new-release-directory`.
4. Inspect the staged tree and `SHA256SUMS.json`. Verify there are no real documents, machine-specific runtime configs, credentials or private benchmark results. Review collected license notices and the Cosmograph restriction.
5. Install the staged release into a fresh isolated workspace and repeat the demo journey before publishing.
6. Initialize/publish the **staged directory** as the GitHub repository, or copy its reviewed source into an already-clean repository. Do not copy this development workspace's hidden directories. Upload the `.tar.gz` and its checksum as release assets.
7. Enable private vulnerability reporting, choose a maintainer contact, and set repository description/topics and the documented support scope. Do not label the combined Cosmograph distribution unrestricted open source.

Commits, pushes and marketplace submission are explicit owner actions. Merging or
pushing to main opts into automated candidate publication after CI passes; stable
release promotion remains a manual GitHub UI action. The local builder never publishes. The clean release marketplace is named
`graf`; the development workspace may use a separate local marketplace name.

The archive contains built UI files for easy installation and source/lockfiles for reproducibility. Python/model dependencies are installed/downloaded separately. Build metadata records file hashes; it is not a cryptographic publisher signature.
