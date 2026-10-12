# GitHub publication

The private repository is provisioned at [3piecechickendinner/agent-reliability-investigator](https://github.com/3piecechickendinner/agent-reliability-investigator). GitHub confirms that the connected account has push permission.

This checkout has that repository configured as `origin`. Publish the committed local `main` branch and verify its remote commit with:

```bash
git push -u origin main
git ls-remote origin refs/heads/main
```

The complete source and Git history are also supplied as a source archive and a Git bundle. To restore the bundle into a new checkout and configure the publication target:

```bash
git clone agent-reliability-investigator.bundle agent-reliability-investigator
cd agent-reliability-investigator
git remote remove origin
git remote add origin https://github.com/3piecechickendinner/agent-reliability-investigator.git
```

Then use the push and verification commands above. Provisioning the repository and restoring a local checkout are separate from a verified publication.
