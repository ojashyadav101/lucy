import { chromium } from "playwright";

const APP_URL = process.env.APP_URL || "http://localhost:4173";

async function main() {
  console.log("\n🧪 Running: Calculator Tests\n");

  const browser = await chromium.launch();
  const page = await browser.newPage();

  try {
    await page.goto(APP_URL, { waitUntil: "networkidle" });

    const display = page.locator('[data-testid="display"]');
    await display.waitFor({ state: "visible", timeout: 10000 });

    // Test: initial display
    let result = await display.textContent();
    if (result !== "0") throw new Error(`Expected "0", got "${result}"`);
    console.log("✅ Initial display shows 0");

    // Test: 7 + 3 = 10
    await page.locator('[data-testid="btn-7"]').click();
    await page.locator('[data-testid="btn-+"]').click();
    await page.locator('[data-testid="btn-3"]').click();
    await page.locator('[data-testid="btn-="]').click();
    result = await display.textContent();
    if (result !== "10") throw new Error(`Expected "10", got "${result}"`);
    console.log("✅ 7 + 3 = 10");

    // Test: Clear
    await page.locator('[data-testid="btn-C"]').click();
    result = await display.textContent();
    if (result !== "0") throw new Error(`Expected "0" after clear, got "${result}"`);
    console.log("✅ Clear works");

    // Test: 9 × 8 = 72
    await page.locator('[data-testid="btn-9"]').click();
    await page.locator('[data-testid="btn-×"]').click();
    await page.locator('[data-testid="btn-8"]').click();
    await page.locator('[data-testid="btn-="]').click();
    result = await display.textContent();
    if (result !== "72") throw new Error(`Expected "72", got "${result}"`);
    console.log("✅ 9 × 8 = 72");

    // Test: Clear + 15 ÷ 3 = 5
    await page.locator('[data-testid="btn-C"]').click();
    await page.locator('[data-testid="btn-1"]').click();
    await page.locator('[data-testid="btn-5"]').click();
    await page.locator('[data-testid="btn-÷"]').click();
    await page.locator('[data-testid="btn-3"]').click();
    await page.locator('[data-testid="btn-="]').click();
    result = await display.textContent();
    if (result !== "5") throw new Error(`Expected "5", got "${result}"`);
    console.log("✅ 15 ÷ 3 = 5");

    // Test: Subtraction 50 - 25 = 25
    await page.locator('[data-testid="btn-C"]').click();
    await page.locator('[data-testid="btn-5"]').click();
    await page.locator('[data-testid="btn-0"]').click();
    await page.locator('[data-testid="btn-−"]').click();
    await page.locator('[data-testid="btn-2"]').click();
    await page.locator('[data-testid="btn-5"]').click();
    await page.locator('[data-testid="btn-="]').click();
    result = await display.textContent();
    if (result !== "25") throw new Error(`Expected "25", got "${result}"`);
    console.log("✅ 50 − 25 = 25");

    // Test: Decimal 3.5 + 1.5 = 5
    await page.locator('[data-testid="btn-C"]').click();
    await page.locator('[data-testid="btn-3"]').click();
    await page.locator('[data-testid="btn-."]').click();
    await page.locator('[data-testid="btn-5"]').click();
    await page.locator('[data-testid="btn-+"]').click();
    await page.locator('[data-testid="btn-1"]').click();
    await page.locator('[data-testid="btn-."]').click();
    await page.locator('[data-testid="btn-5"]').click();
    await page.locator('[data-testid="btn-="]').click();
    result = await display.textContent();
    if (result !== "5") throw new Error(`Expected "5", got "${result}"`);
    console.log("✅ 3.5 + 1.5 = 5");

    // Test: percent 200 % = 2
    await page.locator('[data-testid="btn-C"]').click();
    await page.locator('[data-testid="btn-2"]').click();
    await page.locator('[data-testid="btn-0"]').click();
    await page.locator('[data-testid="btn-0"]').click();
    await page.locator('[data-testid="btn-%"]').click();
    result = await display.textContent();
    if (result !== "2") throw new Error(`Expected "2" for 200%, got "${result}"`);
    console.log("✅ 200 % = 2");

    // Test: toggle sign
    await page.locator('[data-testid="btn-C"]').click();
    await page.locator('[data-testid="btn-5"]').click();
    await page.locator('[data-testid="btn-±"]').click();
    result = await display.textContent();
    if (result !== "-5") throw new Error(`Expected "-5", got "${result}"`);
    console.log("✅ Toggle sign: 5 → -5");

    console.log("\n🎉 All calculator tests passed!\n");
  } catch (err) {
    console.error("\n❌ Test failed:", err instanceof Error ? err.message : err);
    await page.screenshot({ path: "/work/viktor-spaces/calculator/tmp/error.png" });
    const content = await page.locator("body").innerText();
    console.error("Page content:", content);
    console.error("Page URL:", page.url());
    await browser.close();
    process.exit(1);
  }

  await browser.close();
}

main();
