import type { NewIncident } from "./types";

export interface Sample {
  id: string;
  title: string;
  blurb: string;
  build: () => NewIncident;
}

const base = () => ({
  channel_id: "C-WEB",
  thread_ts: (Date.now() / 1000).toFixed(4),
  reporter: "demo-user",
});

export const SAMPLES: Sample[] = [
  {
    id: "checkout",
    title: "Checkout is failing",
    blurb: "Clear outage with a recent deploy. Ends with a proposal for you to approve.",
    build: () => ({
      ...base(),
      summary: "Checkout API is returning elevated 5xx responses",
      service: "checkout-api",
      owner: "payments-oncall",
      symptoms: ["12% error rate", "customer checkout failures"],
      monitoring: { service: "checkout-api", error_rate: 0.12, latency_ms: 2400 },
      github: { repo: "acme/checkout", recent_deploy: "abc123" },
    }),
  },
  {
    id: "auth",
    title: "Sign-in is slow",
    blurb: "Moderate impact on the auth service. A smaller, major-severity proposal.",
    build: () => ({
      ...base(),
      summary: "Sign-in is slow and sometimes failing",
      service: "auth-api",
      owner: "identity-oncall",
      symptoms: ["intermittent login failures"],
      monitoring: { service: "auth-api", error_rate: 0.05 },
    }),
  },
  {
    id: "vague",
    title: "Something is wrong",
    blurb: "Too little information. The agents hand it to a person instead of guessing.",
    build: () => ({ ...base(), summary: "Something seems off with the app, not sure what." }),
  },
];
