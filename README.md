# hoprnet skills

A [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugins/marketplace-reference)
for HOPR agent skills — protocol knowledge and tooling that keeps an agent's
reasoning about HOPR grounded in how the network actually works.

## Plugins

| Plugin | What it does |
| ------ | ------------ |
| `hopr-concepts` | HOPR protocol knowledge aid. Loads ground-truth concepts from RFC-0001–0014 (SPHINX packets, Proof of Relay, tickets and incentives, mixing, sessions, path-finding, PIX) plus Rust-implementation gotchas, so network debugging is correct rather than plausible-but-wrong. |

## Install

```
/plugin marketplace add hoprnet/skills
/plugin install hopr-concepts@hopr-marketplace
```

Then invoke a skill with `/hopr-concepts`, or let it trigger on HOPR debugging
work automatically.

## Local development

Skills live under `tools/<skill-name>/`, each a `SKILL.md` plus any
`references/`. The marketplace manifest is `.claude-plugin/marketplace.json`.

To load a skill directly from a working copy without installing it, symlink it
into a `.claude/skills/` directory on your path — for example, at the root of a
checkout so every repo beneath it picks the skill up:

```
mkdir -p .claude/skills
ln -s ../../skills/tools/hopr-concepts .claude/skills/hopr-concepts
```

## License

MIT — see [LICENSE](LICENSE).
