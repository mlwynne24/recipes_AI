import asyncio
import re
import json
from pathlib import Path

import lancedb
from playwright.async_api import async_playwright

from src.utils.logging import logger
from models.recipes import Recipe, Nutrition

# ─── Setup LanceDB ──────────────────────────────────────────────────────────────
recipes_db_dir = Path("data/recipes_db")
recipes_db_dir.mkdir(parents=True, exist_ok=True)

recipes_db = lancedb.connect(str(recipes_db_dir))

# create table if it doesn’t exist
if not (recipes_db_dir / "recipes.lance").exists():
    recipes_db.create_table(name="recipes", schema=Recipe)

recipes_table = recipes_db.open_table("recipes")


# ─── Scraper ────────────────────────────────────────────────────────────────────
async def scrape_recipes():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        base_url = "https://www.bbcgoodfood.com"
        search_suffix = "/search?q=dinner"

        await page.goto(base_url + search_suffix)
        await asyncio.sleep(2)

        # accept cookies
        consent = page.frame_locator('iframe[title="SP Consent Message"]')
        await consent.get_by_role("button", name="Essential Cookies Only").click()
        await asyncio.sleep(2)

        seen = set()
        # keep loading until no more "Load more"
        while (
            await page.locator(
                "a[data-gtm-class='search-results-load-more-button'] div"
            ).count()
            > 0
        ):
            current_url = page.url

            # gather all recipe links on this page
            hrefs = page.locator(
                "div.search-result--list div.card__content a[data-component='Link']"
            )
            href_list = [
                await hrefs.nth(i).get_attribute("href")
                for i in range(await hrefs.count())
            ]

            for href in href_list:
                if href in seen:
                    continue
                seen.add(href)
                recipe_url = href

                await page.goto(recipe_url)
                await asyncio.sleep(1)

                # ─── build up a dict of only the fields we scrape ────────────────────
                recipe_data: dict[str, any] = {}

                # item id
                try:
                    post = page.locator("div.post.recipe").first
                    recipe_data["id"] = int(await post.get_attribute("data-item-id"))
                    logger.info(f"Item ID: {recipe_data['id']} for {recipe_url}")
                except Exception:
                    logger.warning(f"Item ID missing for {recipe_url}")

                # title
                try:
                    recipe_data["name"] = await page.locator(
                        "h1.heading-1"
                    ).inner_text()
                    logger.info(f"Name: {recipe_data['name']} for {recipe_url}")
                except Exception:
                    logger.warning(f"Title missing for {recipe_url}")

                # serves & difficulty
                try:
                    strongs = page.locator(
                        "div.recipe-cook-and-prep-details__item strong"
                    )
                    serves_text = await strongs.nth(0).inner_text()
                    recipe_data["serves_no"] = float(
                        re.search(r"\d+", serves_text).group()
                    )
                    recipe_data["difficulty"] = await strongs.nth(1).inner_text()
                    logger.info(
                        f"Serves: {recipe_data['serves_no']} and difficulty: "
                        f"{recipe_data['difficulty']} for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Serves or difficulty missing for {recipe_url}")

                # prep time
                try:
                    recipe_data["prep_time"] = (
                        await page.locator(
                            "div.recipe-cook-and-prep-details__item", has_text="Prep"
                        )
                        .locator("time")
                        .get_attribute("datetime")
                    )
                    logger.info(
                        f"Prep time: {recipe_data['prep_time']} for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Prep time missing for {recipe_url}")

                # cook time
                try:
                    recipe_data["cook_time"] = (
                        await page.locator(
                            "div.recipe-cook-and-prep-details__item", has_text="Cook"
                        )
                        .locator("time")
                        .get_attribute("datetime")
                    )
                    logger.info(
                        f"Cook time: {recipe_data['cook_time']} for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Cook time missing for {recipe_url}")

                # rating
                try:
                    script_texts = await page.locator(
                        "script[type='application/ld+json']"
                    ).all_inner_texts()
                    for txt in script_texts:
                        data = json.loads(txt)
                        if data.get("@type") == "Recipe" and data.get(
                            "aggregateRating"
                        ):
                            rating = float(data["aggregateRating"]["ratingValue"])
                            recipe_data["rating"] = int(rating)
                            break
                    logger.info(f"Rating: {recipe_data.get('rating')} for {recipe_url}")
                except Exception:
                    logger.warning(f"Rating missing for {recipe_url}")

                # description
                try:
                    recipe_data["description"] = await page.locator(
                        "#recipe-masthead-description-region p"
                    ).inner_text()
                    logger.info(f"Description captured for {recipe_url}")
                except Exception:
                    logger.warning(f"Description missing for {recipe_url}")

                # features / tags
                try:
                    recipe_data["features"] = await page.locator(
                        ".post-header--masthead__tags-item"
                    ).all_inner_texts()
                    logger.info(f"Features: {recipe_data['features']} for {recipe_url}")
                except Exception:
                    logger.warning(f"Features missing for {recipe_url}")

                # ingredients (required by your model—will set default empty list below)
                try:
                    recipe_data["ingredients"] = await page.locator(
                        "#ingredients-list li.ingredients-list__item"
                    ).all_inner_texts()
                    logger.info(
                        f"Ingredients ({len(recipe_data['ingredients'])}) for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Ingredients missing for {recipe_url}")

                # method steps (required by your model—default below)
                try:
                    recipe_data["method"] = await page.locator(
                        ".method-steps__list-item .editor-content"
                    ).all_inner_texts()
                    logger.info(
                        f"Method steps ({len(recipe_data['method'])}) for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Method steps missing for {recipe_url}")

                # comments
                try:
                    recipe_data["comments"] = await page.locator(
                        "article.reaction.reaction--parent div.mt-reset > p"
                    ).all_inner_texts()
                    logger.info(
                        f"Comments ({len(recipe_data['comments'])}) for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Comments missing for {recipe_url}")

                # nutrition
                try:
                    nut_items = await page.locator(
                        "ul.nutrition-list li"
                    ).all_inner_texts()
                    nd: dict[str, float] = {}
                    for line in nut_items:
                        key = line.split()[0].rstrip(":").lower()
                        val = float(re.search(r"\d+(\.\d+)?", line).group())
                        if key in ("kcal", "calories"):
                            nd["calories"] = val
                        elif key == "fat":
                            nd["fat"] = val
                        elif key == "saturates":
                            nd["saturates"] = val
                        elif key == "carbs":
                            nd["carbs"] = val
                        elif key in ("sugar", "sugars"):
                            nd["sugar"] = val
                        elif key == "fibre":
                            nd["fibre"] = val
                        elif key == "protein":
                            nd["protein"] = val
                        elif key == "salt":
                            nd["salt"] = val
                    if nd:
                        recipe_data["nutrition"] = Nutrition(**nd)
                        logger.info(f"Nutrition: {nd} for {recipe_url}")
                except Exception:
                    logger.warning(f"Nutrition info missing for {recipe_url}")

                # image
                try:
                    img_url = await page.locator(
                        "section.post-header picture img"
                    ).get_attribute("src")
                    img_resp = await context.request.get(img_url)
                    recipe_data["image"] = await img_resp.body()
                    logger.info(f"Image captured for {recipe_url}")
                except Exception:
                    logger.warning(f"Image missing for {recipe_url}")

                # make sure required lists are at least empty
                recipe_data.setdefault("ingredients", [])
                recipe_data.setdefault("method", [])

                # ─── now create & write your model ─────────────────────────────────
                recipe = Recipe(**recipe_data)
                recipes_table.add([recipe.model_dump(exclude_none=True)])

            await page.goto(current_url)
            await asyncio.sleep(1)

            # load more results
            await page.locator(
                "a[data-gtm-class='search-results-load-more-button'] div"
            ).first.click()
            await asyncio.sleep(2)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(scrape_recipes())
