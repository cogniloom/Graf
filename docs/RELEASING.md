# Build and publish a release

Do not publish an existing research workspace wholesale. Private corpora, credentials, benchmark receipts and historical artifacts may be present beside the source code. The release builder copies only named product paths into a fresh staging tree.

1. Run the checks in VERIFICATION.md, including real local ingestion, browser journeys, removal gating and an isolated backup restore.
2. Run `npm ci`, `npm test`, and `npm run build` inside product/ui.
3. From the root, run `python3 scripts/build_release.py --output /absolute/new-release-directory`.
4. Inspect the staged tree and `SHA256SUMS.json`. Verify there are no real documents, machine-specific runtime configs, credentials or private benchmark results. Review collected license notices and the Cosmograph restriction.
5. Install the staged release into a fresh isolated workspace and repeat the demo journey before publishing.
6. Initialize/publish the **staged directory** as the GitHub repository, or copy its reviewed source into an already-clean repository. Do not copy this development workspace's hidden directories. Upload the `.tar.gz` and its checksum as release assets.
7. Enable private vulnerability reporting, choose a maintainer contact, and set repository description/topics and the documented support scope. Do not label the combined Cosmograph distribution unrestricted open source.

GitHub publication, commits, pushes, release uploads and marketplace submission are explicit owner actions. The builder does not perform them. The clean release marketplace is named `docworm`; the development workspace may use a separate local marketplace name.

The archive contains built UI files for easy installation and source/lockfiles for reproducibility. Python/model dependencies are installed/downloaded separately. Build metadata records file hashes; it is not a cryptographic publisher signature.
