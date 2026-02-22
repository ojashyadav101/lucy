import { chromium } from "playwright";

const APP_URL = process.env.APP_URL || "http://localhost:4173";

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 420, height: 700 } });
  await page.goto(APP_URL, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "tmp/calculator.png", fullPage: true });
  console.log("📸 Screenshot saved to tmp/calculator.png");
  await browser.close();
}

main();
