import asyncio
import os

from playwright.async_api import async_playwright


async def capture_screenshots():
    print("Starting browser screenshot capture...")
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        html_path = os.path.abspath("ui_test/index.html").replace("\\", "/")
        file_url = f"file:///{html_path}"
        print(f"Loading URL: {file_url}")

        await page.goto(file_url)
        await page.wait_for_timeout(1000)

        output_dir = os.path.abspath("docs/images")
        os.makedirs(output_dir, exist_ok=True)

        screens = [
            ("screen-1", "screen_1_login.png"),
            ("screen-2", "screen_2_steward_dashboard.png"),
            ("screen-3", "screen_3_dataset_catalog.png"),
            ("screen-4", "screen_4_dataset_profiling.png"),
            ("screen-5", "screen_5_ai_rule_proposals.png"),
            ("screen-7", "screen_7_execution_log.png"),
            ("screen-8", "screen_8_anomaly_dashboard.png"),
            ("screen-10", "screen_10_trend_analysis.png"),
            ("screen-11", "screen_11_viewer_dashboard.png"),
        ]

        for screen_id, filename in screens:
            print(f"Capturing {screen_id} -> {filename}")
            await page.evaluate(f"navigateTo('{screen_id}')")
            await page.wait_for_timeout(500)
            img_path = os.path.join(output_dir, filename)
            await page.screenshot(path=img_path)

        # Capture Modal 6 (Rule Edit)
        print("Capturing modal screen_6_rule_edit_modal.png")
        await page.evaluate("navigateTo('screen-5')")
        await page.evaluate("openEditModal('fare_amount')")
        await page.wait_for_timeout(500)
        await page.screenshot(path=os.path.join(output_dir, "screen_6_rule_edit_modal.png"))
        await page.evaluate("closeModal('modal-rule-edit')")

        # Capture Modal 9 (AI Diagnosis)
        print("Capturing modal screen_9_ai_diagnosis_modal.png")
        await page.evaluate("navigateTo('screen-8')")
        await page.evaluate("openDiagnosisModal()")
        await page.wait_for_timeout(500)
        await page.screenshot(path=os.path.join(output_dir, "screen_9_ai_diagnosis_modal.png"))
        await page.evaluate("closeModal('modal-ai-diagnosis')")

        await browser.close()
        print("Screenshot capture completed successfully!")


if __name__ == "__main__":
    asyncio.run(capture_screenshots())
