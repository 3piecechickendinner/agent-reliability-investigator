# GitHub publication

The implementation is committed locally. On 2026-10-12, both `gh repo create` and `POST /user/repos` were denied by GitHub with `Resource not accessible by integration`. The REST call returned HTTP 403. The connected account is `3piecechickendinner`; no remote repository was created.

Create an empty private repository named `agent-reliability-investigator` in that account, without adding a README or other initial files. From this checkout, publish it with:

```bash
git remote add origin https://github.com/3piecechickendinner/agent-reliability-investigator.git
git push -u origin main
```

Creating a repository needs an account or connection that permits that action. The complete source and Git history are also supplied as a source archive and a Git bundle. To restore the bundle:

```bash
git clone agent-reliability-investigator.bundle agent-reliability-investigator
cd agent-reliability-investigator
git remote remove origin
git remote add origin https://github.com/3piecechickendinner/agent-reliability-investigator.git
git push -u origin main
```
