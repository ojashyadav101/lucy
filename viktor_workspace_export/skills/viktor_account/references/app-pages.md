# Viktor App Pages

All authenticated pages are at `https://app.getviktor.com/...`

## Main Navigation

### Integrations (`/integrations`)
Browse and manage all connected integrations. Users can:
- See all available integrations (Slack, Linear, HubSpot, Google, etc.)
- Connect new integrations
- Configure existing ones (click an integration to see its settings)
- Set up custom API integrations
- Use `?configure=<integrationUrl>` query param to deep-link to a specific integration's config

Each integration has individual tool settings (approval levels, enabled/disabled).

### Integration Detail (`/integrations/<slug>`)
View details for a specific integration:
- Connection status
- Connect/disconnect button
- Tool list with approval settings per tool
- Integration description and capabilities

### Viktor Spaces (`/viktor-spaces`)
View all web apps created by Viktor:
- List of all spaces/apps with their names
- Deployment status (Preview / Production)
- Click into a space to see its Convex dashboard

### Viktor Space Detail (`/viktor-spaces/<projectName>`)
Manage a specific Viktor Space app:
- Embedded Convex dashboard
- Toggle between Production and Preview environments
- View database, functions, and logs

### Usage & Analytics (`/usage`) — Admin only
Credit usage dashboard:
- Total credits used in the selected period
- Today's credit spend
- Average daily credit usage
- Day-by-day spend chart
- Per-thread breakdown (see which conversations and crons cost the most)
- Filter by period: today, this month, last month, last 7/30 days
- Filter by type: all, cron, thread
- Sort by most credits or most recent

Non-admin users are redirected to `/integrations`.

## Settings Pages

### Team Settings (`/settings/team`)
Manage team members:
- View all team members with names, emails, and roles
- Invite new members
- Change member roles (admin / member)
- Remove members
- Only admins can manage team settings

### Scheduled Tasks (`/settings/tasks`)
Manage cron/scheduled tasks:
- View all scheduled tasks with their schedules
- See next run time and last run status
- Enable/disable tasks
- Delete tasks
- Create new scheduled tasks

### Account Settings (`/settings/account`)
Personal account settings:
- Update display name
- Manage notification preferences
- View account email
- Other personal preferences

### Billing & Credits (`/settings/subscription`)
Subscription and billing overview:
- Current plan name and credit allocation
- Credits used / remaining this period
- Billing period dates
- Burn rate and projected runout
- Links to upgrade plan and billing portal

### Plans (`/settings/subscription/plans`)
View and switch between available credit plans:
- All available tiers with pricing
- Current plan highlighted
- Upgrade/downgrade buttons
- Volume discount indicators

### Billing Portal (`/settings/subscription/billing-portal`)
Redirects to Stripe's hosted billing portal where users can:
- View and download invoices
- Update payment method
- Change billing address
- View payment history

## Public Pages

| Page             | Path        | Description          |
| ---------------- | ----------- | -------------------- |
| Support          | `/support`  | Support contact page |
| Privacy Policy   | `/privacy`  | Privacy policy       |
| Terms of Service | `/tos`      | Terms of service     |
| Landing / Slack  | `/`         | Main landing page    |
| Waitlist         | `/waitlist` | Waitlist signup      |

## User Roles

- **Admin**: Full access to all settings, usage analytics, team management, and billing
- **Member**: Access to integrations, spaces, and tasks, but cannot view usage analytics or manage billing/team
