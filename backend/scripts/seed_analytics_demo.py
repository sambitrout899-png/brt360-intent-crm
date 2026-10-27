"""Seed a telecom demo history into one organization for Analytics & Reports.

    python -m scripts.seed_analytics_demo --tenant-slug default [--customers 320 --months 24 --seed 42]

Two years of leads, deals, orders, bills, payments, usage, tickets, surveys,
fraud and credit alerts with realistic patterns (growth, seasonality, churn
warning signs). Safe to run twice: it does nothing once the demo customers
exist."""
from app.analytics.demo_data import main

if __name__ == "__main__":
    main()
