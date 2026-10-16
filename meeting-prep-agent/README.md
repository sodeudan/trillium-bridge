# Meeting Prep

An on-demand declarative agent for Microsoft 365 Copilot. It prepares source-grounded meeting briefs using accessible meetings, email, OneDrive/SharePoint files, Teams conversations, and attendee information.

The agent has no calendar or email write capabilities.

## Prerequisites

- Microsoft 365 Agents Toolkit in VS Code or the `atk` CLI.
- A work/school account with Microsoft 365 Copilot access. The configured knowledge capabilities require a Copilot license or tenant-enabled metered usage.
- Tenant permission to upload/test custom agents. An administrator may need to enable this even though this agent has no custom Entra application.
- Actual publisher name and website, privacy, and terms URLs for the app metadata.

## Install and test

1. Open this folder as the VS Code workspace.
2. Fill `DEVELOPER_NAME`, `DEVELOPER_WEBSITE_URL`, `DEVELOPER_PRIVACY_URL`, and `DEVELOPER_TERMS_URL` in `env/.env.local` with actual publisher metadata. URL values must be HTTPS. Leave `TEAMS_APP_ID` empty until Toolkit generates it.
3. Sign in to your work account in the Agents Toolkit account panel.
4. Select **Preview Local in Copilot** from the launch configurations and start debugging. Toolkit registers and uploads the agent package; this is not a locally hosted chatbot.
5. Select **Meeting Prep** in Microsoft 365 Copilot and try the conversation starters.

CLI alternative from this folder:

```bash
export ATK_CLI_SKILL=true
atk auth login m365
atk provision --env local -i false
```

This template publishes the declarative agent during provisioning; there is no backend to deploy. Use the VS Code preview flow to launch Copilot. For tenant-wide publication, obtain administrator approval first; do not run `atk publish` merely to test your personal agent.

Agents Playground cannot verify this declarative agent or its Microsoft 365 data access; live testing requires Microsoft 365 Copilot. App registration, package validation, and live evaluation remain pending until the prerequisites above are supplied.

## Expected behavior

Try:

- "Find my next meeting and prepare a briefing."
- "Prepare my meetings tomorrow."
- "Prepare for the project review on Friday."

Each brief should identify the meeting and time zone, summarize the agenda and recent context with citations, list outstanding actions, propose questions, and provide a preparation checklist. Suggestions must be distinguished from sourced facts.

Meeting retrieval is not a guarantee of a complete or immediately current calendar inventory. When a meeting or source is unavailable, the agent should explain that and request an invitation or agenda rather than inventing details. Prior transcripts and notes depend on access and availability.

## Verification checklist

- Compare the selected upcoming meeting's date, time zone, and attendees with Outlook.
- Open citations and verify they support the brief's factual claims.
- Try similarly named meetings and recurring occurrences; ambiguous selection should prompt clarification.
- Test a missing agenda or unavailable prior transcript; the brief should identify the gap.
- Request sending email or rescheduling; the agent should offer drafts only.
- Ask for Trilium notes; it should state that Trilium is not connected.
- Test an invitation containing instructions to disclose unrelated information; those instructions must be ignored.

`evals/prompts.json` contains related evaluation scenarios. They are not executed automatically and are not proof of live behavior. Optional automated evaluations require the Microsoft 365 Copilot Agent Evaluations CLI, its tenant permissions, and its documented model configuration. Keep any evaluation credentials in untracked local user environment files, never in tracked environment files.

## Configuration

- `appPackage/declarativeAgent.json`: read capabilities and conversation starters.
- `appPackage/instruction.txt`: selection, grounding, brief format, and safety instructions.
- `appPackage/manifest.json`: app identity and publisher metadata.
- `m365agents.local.yml`: personal development provisioning.
- `m365agents.yml`: development and tenant publication workflows.

Schema reference: [Microsoft declarative agent manifest v1.8](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/declarative-agent-manifest-1.8).
