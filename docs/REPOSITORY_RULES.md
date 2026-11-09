# Repository rules

`.github/rulesets/main.json` is a prepared repository ruleset. Committing the file
does not activate branch protection. A repository administrator must import it in
GitHub's Rulesets settings or create it with:

```sh
gh api --method POST repos/Corrgan/multichat/rulesets --input .github/rulesets/main.json
```

Before activation, run the Tests workflow once on a pull request and confirm its
three check names match the JSON. `integration_id: 15368` restricts these checks
to GitHub Actions. The rules require a pull request with resolved review threads
and successful checks against the latest base branch, prevent force pushes and
branch deletion, and have no bypass actors. Zero mandatory approvals allows the
sole maintainer to merge their own tested pull requests. Import only once; update
the existing ruleset afterward to avoid duplicates.

Activation requires repository Administration write access; see the
[GitHub REST ruleset documentation](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset).
During the local audit, the connected `nepunuk` account had read access only to
`Corrgan/multichat`, and `git push --dry-run` returned HTTP 403. No remote rule,
branch, tag or release was changed.
