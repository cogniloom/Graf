# Build and publish a release

## GitHub Actions

`.github/workflows/release.yml` runs on every push to `main`, including PR
merges, squash merges, rebase merges, and direct pushes. After Python/dashboard
tests and the dashboard build pass, it packages the allowlisted application and
creates a Git tag and GitHub **pre-release** automatically. No manual tagging is
required in Orca or a shell.

Tags use `v<major>.<minor>.<build-number>rc`. For example, workflow run 5
in series `0.1` creates `v0.1.5rc` and `graf-0.1.5rc.tar.gz` with a `.sha256`
checksum. Run 6 creates `v0.1.6rc`, and run 7 creates `v0.1.7rc`, whether or not
you promoted an earlier build. There is no separate RC counter. Build numbers are
GitHub workflow run numbers: failed/manual runs can leave gaps, reruns reuse their
number, and changing the major/minor series does not reset the workflow counter.
The tag points to the exact tested commit. Each main push runs independently.

### Promote a candidate using GitHub's web UI

1. Open the repository's **Releases** page and edit the tested candidate.
2. Change its tag from, for example, `v0.1.5rc` to `v0.1.5`, creating the stable
   tag at the **same candidate commit**. Do not target a newer `main` commit.
3. Update the display title, clear **Set as a pre-release**, and optionally select
   **Set as the latest release**. Save the release.

Changing only the display title does not rename the Git tag. Promotion does not
rebuild or rename downloadable assets: they retain their `rc` filenames and
checksums. The next builds are `v0.1.6rc`, then `v0.1.7rc`; you can promote either.
You may also keep the original tag and only clear the prerelease flag.

### Control major and minor versions

The default series comes from the major/minor portion of the source package
version. To choose another series, open **Settings → Secrets and variables →
Actions → Variables** and set **`RELEASE_SERIES`** to, for example, `0.2` or `1.0`.
The next build number 8 then produces `v0.2.8rc` or `v1.0.8rc`. Delete the variable
to return to the source series. Use `X.Y` only; the patch component always comes
from the workflow run number. Do not change `RELEASE_SERIES` when rerunning an old build if you want
to preserve its original tag.

For a source-controlled series change, run
`python3 scripts/set_release_version.py 0.2.0`, review the five changed metadata
files, and include them in your normal PR. The source patch component is replaced
by the build number in the disposable CI checkout. Python, dashboard, plugin and
both lockfiles receive the same numeric version before tests; the workflow never
commits these changes. The `rc` suffix is used for Git tags and archive names;
package metadata stays numeric. Manual runs build artifacts without publishing.

Published candidates (including candidates marked stable with the same tag) are
left unchanged on reruns. Unfinished drafts resume their asset uploads. If a
candidate was renamed to its stable tag, rerunning its publication is blocked
rather than recreating that candidate. A new workflow run uses its new build
number. The publisher rejects versions at or below a published stable version,
including legacy `-rc.N`/`-rcN` tags that were promoted to stable. Release API
errors stop publication rather than guessing.

The publisher rechecks release status after building. GitHub does not provide an
atomic lock between manual promotion and publication, so avoid promoting during
the brief publication step; an overlapping build may need a new run.

Both jobs select only the exact `self-hosted` runner label. The build runner must
provide Linux with Docker job-container support and network access to the image
and package repositories, plus the standard GitHub Actions runner prerequisites.
The build runs in `node:22-bookworm-slim` with `no-new-privileges` enabled. It
installs packages as the container's root user without `sudo` or host package
changes. The build job installs Git, CA certificates, and
`poppler-utils tesseract-ocr tesseract-ocr-eng libseccomp2 libarchive13 antiword ffmpeg` before checking
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
then verify and extract (substitute your build number):

```sh
sha256sum --check graf-0.1.42rc.tar.gz.sha256
tar -xzf graf-0.1.42rc.tar.gz
cd graf-0.1.42rc
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
