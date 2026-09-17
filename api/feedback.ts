import type { VercelRequest, VercelResponse } from '@vercel/node';

interface FeedbackRequest {
  name?: string;
  email?: string;
  message: string;
}

export default async function handler(
  req: VercelRequest,
  res: VercelResponse
) {
  // Only allow POST
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const data: FeedbackRequest = req.body;

    // Validate required fields
    if (!data.message || data.message.trim().length === 0) {
      return res.status(400).json({ error: 'Message is required' });
    }

    // Get GitHub token from environment variable
    const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
    const GITHUB_REPO = process.env.GITHUB_REPO || 'xu75/quant-strategy';

    if (!GITHUB_TOKEN) {
      console.error('GITHUB_TOKEN not configured');
      return res.status(500).json({
        error: 'Feedback service not configured. Please contact site administrator.'
      });
    }

    // Construct issue body
    const issueBody = `
**Message:**
${data.message}

---
${data.name ? `**Submitted by:** ${data.name}\n` : ''}${data.email ? `**Email:** ${data.email}\n` : ''}
**Submitted at:** ${new Date().toISOString()}
**Source:** Website Feedback Form
    `.trim();

    // Create GitHub Issue via API
    const response = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/issues`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${GITHUB_TOKEN}`,
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      body: JSON.stringify({
        title: `[Website Feedback] ${data.message.slice(0, 60)}${data.message.length > 60 ? '...' : ''}`,
        body: issueBody,
        labels: ['feedback', 'from-website'],
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error('GitHub API error:', response.status, errorText);
      return res.status(500).json({
        error: 'Failed to submit feedback. Please try again later.'
      });
    }

    const issue = await response.json();

    return res.status(200).json({
      success: true,
      issueUrl: issue.html_url,
      issueNumber: issue.number
    });

  } catch (error) {
    console.error('Feedback submission error:', error);
    return res.status(500).json({ error: 'Internal server error' });
  }
}
