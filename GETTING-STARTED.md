# Getting Started with Your Own A2rchi App
==========================================
1. Set up authentication through OIDC
- we walk through setting up login w/GitHub
- other providers (google, microsoft, apple, even vanilla email/password) are also supported through this flow
2. TODO

## Set Up Authentication Through OIDC w/Github
- following these docs (the first links to the second):
    - https://cloud.google.com/identity-platform/docs/web/github
    - https://cloud.google.com/identity-platform/docs/sign-in-user-email
- go to cloud.google.com and create a project
    - https://cloud.google.com/identity-platform/docs/web/oidc
- enable billing
    - you will need to add billing to the account (note: authentication is free for 0-49 monthly active users (MAUs) and then $0.015 per user for 50+)
- go to github.com and create a project (need more details)
    - generate client secret to go along w/client ID
    - fill in redirect URL using one shown in google
