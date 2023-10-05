# Getting Started with Your Own A2rchi App
==========================================
1. Set up authentication through OIDC
- we walk through setting up login w/GitHub
- other providers (google, microsoft, apple, even vanilla email/password) are also supported through this flow
2. TODO

## (TODO: rewrite or delete) Set Up Authentication Through OIDC w/Github
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


## Set Up Authentication Through Google (gmail)
- TODO (but basically register client, and pass in client secret and id)

## Set Up Authentication Through OIDC w/MIT Touchstone
- Create a registration for your app by going to https://oidc.mit.edu/manage/dev/dynreg.
- Login in w/your MIT credentials and click the button to "Register a new client"
- Create a name for your client (e.g. "MyA2rchi")
- Set the redirect URI to be `https://<your-domain>:5000/login/callback`
- Optionally, you may provide a link to a logo, terms of service, policy, and home page
- Leave the settings on the other tabs set to their defaults
- Click "Save"
- After ~ 30 seconds - 1 minute, the webpage should respond with:
    - `Client ID`
    - `Client Secret`
    - `Client Configuration URL`
    - `Registration Access Token`
- Copy your (client and secret to where?)