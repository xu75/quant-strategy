# GitHub Feedback Integration Setup

## Overview

The website includes a feedback form that automatically creates GitHub Issues without requiring users to have a GitHub account.

## Architecture

```
User fills form on /feedback
  ↓
POST /api/feedback (Vercel Serverless Function)
  ↓
GitHub API creates Issue
  ↓
Success response with Issue URL
```

## Configuration Required

### 1. Create GitHub Personal Access Token

1. Go to GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Name: `quant-strategy-feedback`
4. Scopes: Select **`public_repo`** (for public repos) or **`repo`** (for private repos)
5. Click "Generate token"
6. **Copy the token immediately** (you won't be able to see it again)

### 2. Configure Vercel Environment Variables

#### Option A: Via Vercel Dashboard (Recommended)

1. Go to your Vercel project: https://vercel.com/xu75/quant-strategy
2. Navigate to **Settings** → **Environment Variables**
3. Add the following variables:

| Name | Value | Environments |
|------|-------|--------------|
| `GITHUB_TOKEN` | `ghp_xxxxxxxxxxxxx` (your token) | Production, Preview, Development |
| `GITHUB_REPO` | `xu75/quant-strategy` | Production, Preview, Development |

4. Click **Save**
5. **Redeploy** the site for changes to take effect

#### Option B: Via Vercel CLI

```bash
vercel env add GITHUB_TOKEN
# Paste your token when prompted

vercel env add GITHUB_REPO
# Enter: xu75/quant-strategy
```

### 3. Local Development Setup

Create `.env` file in `site/` directory:

```bash
# site/.env
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
GITHUB_REPO=xu75/quant-strategy
```

**Important**: `.env` is already in `.gitignore`. Never commit tokens to git!

## Testing

### Local Testing

```bash
cd site
pnpm dev
```

Visit http://localhost:4321/feedback and submit a test feedback.

### Production Testing

After deployment, visit:
```
https://quant-strategy.mesh-hub.xyz/feedback
```

Submit test feedback and verify:
1. Success message appears
2. GitHub Issue is created: https://github.com/xu75/quant-strategy/issues
3. Issue has labels: `feedback`, `from-website`

## Security Notes

1. **Token Permissions**: Use minimal scope (`public_repo` for public repos)
2. **Token Storage**: Never commit tokens to version control
3. **Token Rotation**: Rotate tokens periodically (recommend every 6-12 months)
4. **Rate Limiting**: GitHub API has rate limits (5000 requests/hour for authenticated requests)

## Troubleshooting

### "Feedback service not configured"
- Check `GITHUB_TOKEN` is set in Vercel environment variables
- Redeploy after adding environment variables

### "Failed to submit feedback"
- Check token has correct permissions (`public_repo` or `repo`)
- Check `GITHUB_REPO` value is correct (`owner/repo` format)
- Check GitHub API status: https://www.githubstatus.com/

### Issues created but labels missing
- Ensure the repository has `feedback` and `from-website` labels created
- Or remove the `labels` field from the API call in `feedback.ts`

## API Endpoint

**Endpoint**: `POST /api/feedback`

**Request Body**:
```json
{
  "name": "John Doe (optional)",
  "email": "john@example.com (optional)",
  "message": "Your feedback here (required)"
}
```

**Success Response** (200):
```json
{
  "success": true,
  "issueUrl": "https://github.com/xu75/quant-strategy/issues/123",
  "issueNumber": 123
}
```

**Error Response** (400/500):
```json
{
  "error": "Error message"
}
```

## Maintenance

- **Token Expiration**: Check token validity if feedback stops working
- **Label Management**: Ensure `feedback` and `from-website` labels exist in the repo
- **Monitoring**: Check GitHub Issues periodically for new feedback

## Future Enhancements

Potential improvements:
- Add spam protection (reCAPTCHA)
- Email notifications for new feedback
- Admin dashboard for feedback management
- Auto-close stale feedback issues
