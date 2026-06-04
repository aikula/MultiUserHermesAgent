# 13. Hermes Skills Usage Specification

## Цель

Использовать возможности Hermes по умолчанию и не изобретать заново то, что upstream уже умеет.

Для учебного MVP нужно показать студентам три уровня навыков:

1. built-in Hermes capabilities;
2. local manager skills в нашем webapp;
3. external tools/MCP для расширения агента.

## What Hermes already provides upstream

According to Hermes docs and README, upstream Hermes has:

- persistent memory across sessions;
- autonomous skill creation and skill improvement;
- scheduled automations via built-in cron;
- messaging gateway support;
- 60+ built-in tools configurable through Hermes tools;
- MCP integration;
- web search, browser, image generation, TTS through Tool Gateway options;
- skills compatible with agentskills.io.

## Recommendation for this project

Do not duplicate upstream learning loop. For our training MVP, expose and explain it.

### Use directly from Hermes when possible

- memory and skill concept;
- Telegram gateway behavior;
- model/tool configuration;
- built-in cron idea;
- MCP-compatible integration pattern;
- web search/browser from Tool Gateway if credentials are available.

### Implement in our webapp for training UX

- Files tab;
- account lifecycle;
- teacher-friendly manager skills;
- simple scheduler UI;
- simple automation UI;
- SearxNG parser fallback;
- email/calendar approval cards;
- safe per-user workspace UI.

## Skill library for management training

Create folder:

`webapp/app/skills/library/`

Initial files:

- `meeting_followup.md`
- `task_extraction.md`
- `decision_memo.md`
- `risk_review.md`
- `email_reply.md`
- `daily_digest.md`
- `delegation_plan.md`
- `stakeholder_map.md`
- `weekly_status_report.md`
- `research_brief.md`

Each skill file format:

```md
# Skill name

## When to use
...

## Inputs
...

## Output format
...

## Quality checklist
...

## Example prompt
...
```

## UI

Add `Skills` section, can be simple in first version:

- list enabled skills;
- show skill description;
- button `Use in chat`;
- button `Copy example prompt`.

Do not build full marketplace in demo sprint.

## Agent prompt integration

`build_system_prompt()` should include compact list of enabled skill names and short routing hints, not full long skill text every time.

When user explicitly selects skill, inject full skill text only for that turn.

Reason: full skills in every prompt will waste tokens. Humanity has invented enough ways to burn money; this one is optional.

## Skill activation flow

1. User opens Skills tab.
2. Selects `Meeting Follow-up`.
3. UI inserts prompt into chat.
4. Agent returns structured output.
5. User can save output as file.
6. User can create reminder or email from output.

## Acceptance checklist

- [ ] Skills tab exists or skills are visible in profile/settings.
- [ ] At least 10 manager skills are available as markdown files.
- [ ] User can insert skill prompt into chat.
- [ ] Full skill text is not injected into every prompt by default.
- [ ] Skill output can be saved to Files tab.
- [ ] Email/calendar actions from skills use approval flow.

## Tests

- `test_skill_library_loads`
- `test_enabled_skills_list_compact_in_prompt`
- `test_full_skill_injected_only_when_selected`
- `test_skill_prompt_inserted_to_chat`
- `test_skill_output_can_be_saved_as_file`

## Demo script

1. Open Skills.
2. Choose `Decision Memo`.
3. Ask: `Что выбрать для MVP: Telegram-first or Web-first?`
4. Save result to `decision_memo.md`.
5. Choose `Meeting Follow-up`.
6. Paste rough meeting notes.
7. Create follow-up email with approval.
