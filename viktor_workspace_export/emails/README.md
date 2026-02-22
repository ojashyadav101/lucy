# Email

Your email address: **serprisingly@viktor-mail.com**

## Directory Structure

- `inbox/` - Received emails (one .md file per email)
- `sent/` - Sent emails (logged after sending)
- `attachments/` - Downloaded attachments

## Tools

- `coworker_send_email(to=[...], subject="...", body="...")` - Send an email
- `coworker_get_attachment(internal_url="...", filename="...")` - Download an attachment

To read emails, use file tools like `cat /work/emails/inbox/<email_id>.md`.

## Working with Attachments

Email attachments are listed in the frontmatter with a `_internal_url` field.
**Do not try to download this URL directly** - it requires API authentication.

Use the `coworker_get_attachment` tool with the `_internal_url` and filename:
```
coworker_get_attachment(internal_url="https://...", filename="report.pdf")
```

The tool will download the file to `/work/emails/attachments/`.
