// Diagnostic for CI: log in once and print the browser console and failed requests.
import { chromium } from "@playwright/test"

const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:8000"
const browser = await chromium.launch()
const page = await browser.newPage()
page.on("console", (m) => console.log(`[console.${m.type()}] ${m.text()}`))
page.on("pageerror", (e) => console.log(`[pageerror] ${e.message}`))
page.on("requestfailed", (r) => console.log(`[requestfailed] ${r.method()} ${r.url()} ${r.failure()?.errorText}`))
page.on("response", (r) => { if (r.url().includes("/api/")) console.log(`[api] ${r.status()} ${r.url()}`) })
await page.goto(`${base}/login`)
await page.getByTestId("email-input").fill(process.env.FIRST_SUPERUSER ?? "admin@example.com")
await page.getByTestId("password-input").fill(process.env.FIRST_SUPERUSER_PASSWORD ?? "changethis")
await page.getByRole("button", { name: "Log In" }).click()
await page.waitForTimeout(5000)
console.log(`[url after login] ${page.url()}`)
console.log(`[localStorage token] ${await page.evaluate(() => (localStorage.getItem("access_token") ?? "none").slice(0, 12))}`)
await browser.close()
