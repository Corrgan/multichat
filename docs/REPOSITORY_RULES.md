# Repository rules

Two prepared rulesets live in `.github/rulesets/`. Committing the files does not activate
anything – a repository administrator imports them once:

**GitHub → Settings → Rules → Rulesets → New ruleset → Import a ruleset**, or

```sh
gh api --method POST repos/Corrgan/multichat/rulesets --input .github/rulesets/main.json
gh api --method POST repos/Corrgan/multichat/rulesets --input .github/rulesets/release-tags.json
```

| File | Effect |
|---|---|
| `main.json` | `main` cannot be deleted or force-pushed. Ordinary pushes and merges work as before. |
| `release-tags.json` | Release tags (`v*`) cannot be moved or deleted once they exist; creating new tags works as before. A published release always points to the commit it was built from. |

Import each file only once; change the existing ruleset afterwards to avoid duplicates.

## Stricter, if wanted later

The project is maintained by one person who tests on a branch and then fast-forwards
`main`, so pull requests are not required. To enforce "only tested commits reach `main`",
add a *required status checks* rule to the `main` ruleset with the check `tests` (GitHub
Actions): a commit can then only be pushed to `main` after the Tests workflow passed for it
on its branch. Requiring pull requests on top of that is possible, but changes the
workflow for every change.
