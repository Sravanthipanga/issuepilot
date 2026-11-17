FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY issuepilot ./issuepilot
COPY sample_repo ./sample_repo
COPY sample_issues ./sample_issues

RUN pip install --no-cache-dir .

ENTRYPOINT ["issuepilot"]
CMD ["demo"]
