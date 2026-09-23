import "@testing-library/jest-dom/vitest";

// Component tests intentionally exercise the fixed backend-owned fixtures.
// Deployments still require an explicit NEXT_PUBLIC_MODELFC_API_MODE.
process.env.NEXT_PUBLIC_MODELFC_API_MODE ??= "mock";
