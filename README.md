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

In Claude Code, add the marketplace and install the plugin:

```text
/plugin marketplace add hoprnet/skills
/plugin install hopr@hopr-marketplace
```

The plugin validator reports the skill names `/hopr:concepts` and
`/hopr:release`. For local development, start Claude Code from this checkout
with `claude --plugin-dir plugins/hopr`.

### Codex

Add the marketplace and plugin using the Codex CLI:

```sh
codex plugin marketplace add hoprnet/skills
codex plugin add hopr@hopr-marketplace
```

The exact skill selection and invocation syntax has not been verified in an
authenticated Codex session.

### Pi

Install the Git package:

```sh
pi install git:github.com/hoprnet/skills
```

Pi exposes the skills as `/skill:concepts` and `/skill:release`. To install a
local checkout for the current project, run `pi install ./ -l` in its root.

### OpenCode

Clone this repository and add its skills directory to your `opencode.json`,
substituting the absolute path to your checkout:

```json
{
  "skills": ["/absolute/path/to/skills/plugins/hopr/skills"]
}
```

OpenCode discovers the IDs `concepts` and `release`. Its documented way to
request a skill is to mention `@concepts` or `@release` in a prompt; actual
model invocation has not been verified here.

### Verification and collisions

These checks used macOS on Apple Silicon. A discovery result does not imply
that an authenticated model session executed the skill.

| Client and version | Checked | Not yet checked |
| --- | --- | --- |
| Claude Code 2.1.327 | Plugin validation reports `hopr:concepts` and `hopr:release`. | Marketplace installation and invocation in an authenticated session. |
| Codex 0.169.0-alpha.11.1 | CLI exposes the marketplace and plugin commands above. | Marketplace installation, discovery, skill names, and invocation. |
| Pi 0.73.1 | Local package installation and discovery; the command list exposes `/skill:concepts` and `/skill:release`. | Git package installation and model execution. |
| OpenCode 1.19.6 | Configured skill directory discovers both IDs. | Model invocation with `@concepts` or `@release`. |

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
