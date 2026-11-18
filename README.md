# HOPR agent skills

The `hopr` plugin contains skills for working with the HOPR network and its
repositories. Each skill has one canonical copy in `plugins/hopr/skills/`.

| Skill | Purpose |
| --- | --- |
| `concepts` | HOPR protocol concepts, RFCs, and implementation details for reasoning about the network. |
| `release` | Plan and propagate Rust releases across HOPR repositories, including dependency repins. |

The skills can also be selected automatically when relevant to a task. Review
the release skill's instructions before performing operations that change
repositories or publish packages.

## Install and use

Client names differ: Claude Code namespaces the skills under `hopr`; Pi and
OpenCode use the short skill names. The commands below describe the clients'
documented interfaces. The verification table states what was actually checked.

### Claude Code

In Claude Code, add this repository ([hoprnet/skills](https://github.com/hoprnet/skills))
as a marketplace and install its `hopr` plugin:

```text
/plugin marketplace add https://github.com/hoprnet/skills.git
/plugin install hopr@hopr-marketplace
```

The plugin validator reports the skill names `/hopr:concepts` and
`/hopr:release`. For local development, start Claude Code from this checkout
with `claude --plugin-dir plugins/hopr`.

### Codex

Add this repository's marketplace and plugin using the Codex CLI:

```sh
codex plugin marketplace add hoprnet/skills
codex plugin add hopr@hopr-marketplace
```

An installed copy has been checked in Codex: `concepts` could read its bundled
implementation reference, and `release` could read its script and run `--help`.
The CLI app server's `skills/list` reports their names as `hopr:concepts` and
`hopr:release`. The exact prompt invocation syntax has not been recorded.

The release script requires Python 3.11 or newer for `tomllib`. If the default
`python3` is older, run the script with an explicitly selected interpreter, such
as `python3.12`. The Codex check ran only `--help`, not a release plan.

### Pi

Install the Git package:

```sh
pi install git:github.com/hoprnet/skills
```

Pi exposes the skills as `/skill:concepts` and `/skill:release`. To install a
local checkout for the current project, run `pi install ./ -l` in its root.

### OpenCode

Clone [hoprnet/skills](https://github.com/hoprnet/skills) and add its skills directory to your `opencode.json`,
substituting the absolute path to your checkout:

```json
{
  "skills": ["/absolute/path/to/skills/plugins/hopr/skills"]
}
```

OpenCode discovers the IDs `concepts` and `release`. Mention `@concepts` or
`@release` in a prompt to load the corresponding skill.

### Verification and collisions

These checks used macOS on Apple Silicon. A discovery result does not imply
that an authenticated model session executed the skill.

| Client and version | Checked | Not yet checked |
| --- | --- | --- |
| Claude app (version not recorded) | Both skills worked. `concepts` read both bundled references; `release` located its script and references and ran the script's `--help` successfully. | Exact invocation syntax and a client-initiated release plan. |
| Claude Code 2.1.327 | Plugin validation reports `hopr:concepts` and `hopr:release`. | Marketplace installation and invocation in an authenticated session. |
| Codex | An installed copy provided both skills; `concepts` read its bundled reference, and `release` read its script and ran `--help` with Python 3.12. The CLI commands above were checked with 0.169.0-alpha.11.1; the version used for the installed-copy check was not recorded. | Exact invocation syntax and a client-initiated release plan. |
| Pi 0.73.1 | Local package installation and discovery; the command list exposes `/skill:concepts` and `/skill:release`. A Pi session read both concepts references and ran the release script's `--help` with Python 3.12 (exit 0). Installation from a local Git server discovered both skills. | GitHub package installation, the exact commands used in the Pi session, and a client-initiated release plan. |
| OpenCode 1.19.6 | `@concepts` loaded the skill and read both references; `@release` loaded the skill and ran the script's `--help` with Python 3.12 (exit 0). | A client-initiated release plan. |

Command-only discovery was rechecked on 2026-11-18 without a model request:

| Client and version | Discovery check and result |
| --- | --- |
| Claude Code 2.1.146 | Loaded this checkout with `--plugin-dir plugins/hopr`. The stream-JSON `initialize` control response listed `concepts` and `release`, each with a `(hopr)` description prefix. Both plugin and marketplace validation passed. This control API returns short names; it does not verify slash-command invocation. |
| Codex 0.169.0-alpha.11.1 | With this checkout configured as a local marketplace, app-server `skills/list` with `forceReload: true` listed `hopr:concepts` and `hopr:release` from the previously installed `1.0.0` cache. Installation of the revised `0.2.0` package remains unchecked. |
| Pi 0.73.1 | Started RPC mode with `--skill plugins/hopr/skills`; `get_commands` returned `skill:concepts` and `skill:release`, both pointing to this checkout. |
| OpenCode 1.19.6 | With the skills directory configured as above, `opencode debug skill` returned `concepts` and `release`, both pointing to this checkout. |

The release suite also passed all 245 tests on Python 3.11 and 3.12 with isolated
Git configuration (`GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`). An actual
publish through the shortened release skill description remains untested; it
requires a selected release target and approval of its release plan.

Separately from the client sessions, the release script was run from a copied
plugin against `hoprd@main` in a temporary workspace. Its anonymous dry run
completed with a JSON plan; it did not execute releases or open pull requests.

Because Pi and OpenCode do not namespace these skills, another skill named
`concepts` or `release` can shadow one. In the tested versions, Pi selected the
first skill found and emitted a collision warning; an OpenCode project-local
skill took precedence over a skill from the explicitly configured directory.
Check the client diagnostics and loaded skill paths if names collide.

## Migration from the separate Claude plugins

This is a breaking change for users of the former `hopr-concepts` and
`hopr-release` plugins. Uninstall both old plugins, update the marketplace if
needed, and install `hopr`:

```text
/plugin uninstall hopr-concepts@hopr-marketplace
/plugin uninstall hopr-release@hopr-marketplace
/plugin marketplace update hopr-marketplace
/plugin install hopr@hopr-marketplace
```

The old plugin commands are replaced by `/hopr:concepts` and `/hopr:release`.

## Repository layout

- `plugins/hopr/skills/`: skill instructions, references, and release scripts.
- `plugins/hopr/.claude-plugin/` and `plugins/hopr/.codex-plugin/`: plugin
  manifests.
- `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`:
  client-specific marketplace entries for the same plugin.
- `package.json`: Pi package metadata pointing to the same skill directories.

## License

MIT — see [LICENSE](LICENSE).
