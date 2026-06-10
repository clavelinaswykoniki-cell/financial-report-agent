# Security Policy

## API Key Handling

- Put real API keys in `.env` or shell environment variables only.
- Never commit `.env`, `.env.local`, provider credentials, generated caches, or runtime reports that may contain sensitive prompts.
- `.env.example` contains placeholders only and is safe to commit.
- The application reads keys at runtime and does not write provider keys into Markdown or JSON reports.
- The local dashboard passes form-submitted API keys directly to the current analysis call; it does not persist them to disk or mutate global environment variables.

## Supported Secret Variables

- `OPENAI_API_KEY`
- `DEEPSEEK_API_KEY`
- `FIN_AGENT_API_KEY`
- `FIN_AGENT_BASE_URL`

## Before Publishing

Run a local secret scan before pushing:

```bash
rg -n "sk-[A-Za-z0-9_-]{20,}|(OPENAI|DEEPSEEK|FIN_AGENT)_API_KEY=[\"']?sk-[A-Za-z0-9_-]{20,}" .
```

The expected result is no matches. Use `YOUR_API_KEY` style placeholders in documentation.

## Financial Safety

This project is for financial statement analysis and learning. It does not place trades, produce target prices, or provide investment advice.
