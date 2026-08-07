# NSE Paper Trader Cloud V6
Render Web Service = mobile dashboard.
Render Free Postgres = persistent database for the 15-day experiment.
GitHub Actions = scheduled scanner every 5 minutes on weekdays during NSE market hours.

Render free Postgres currently expires after 30 days, which is sufficient for the planned 15-day test. Free web services can sleep when idle; that does not stop GitHub Actions from updating the database.

Render web start command:
`streamlit run app.py --server.port $PORT --server.address 0.0.0.0`

Add `DATABASE_URL` to the Render web service and as a GitHub repository secret with the same Render Postgres external URL.

GitHub scheduled workflows can be delayed, so this is not an exchange-grade real-time feed. NSE public endpoints may rate-limit or change. No real orders are placed.
