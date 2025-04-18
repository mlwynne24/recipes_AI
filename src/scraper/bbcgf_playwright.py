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

                # defaults
                item_id = None
                name = ""
                serves_no = 0
                difficulty = ""
                prep_time = None
                cook_time = None
                rating = None
                description = ""
                features = []
                ingredients = []
                method = []
                comments = []
                nutrition = Nutrition(
                    calories=0.0,
                    fat=0.0,
                    saturates=0.0,
                    carbs=0.0,
                    sugar=0.0,
                    fibre=0.0,
                    protein=0.0,
                    salt=0.0,
                )
                image_bytes = b""

                # ─── scrape fields with try/except ─────────────────────────────────
                try:
                    post = page.locator("div.post.recipe").first
                    item_id = int(await post.get_attribute("data-item-id"))
                    logger.info(f"Item ID: {item_id} for {recipe_url}")
                except Exception:
                    logger.warning(f"Item ID missing for {recipe_url}")

                try:
                    name = await page.locator("h1.heading-1").inner_text()
                    logger.info(f"Name: {name} for {recipe_url}")
                except Exception:
                    logger.warning(f"Title missing for {recipe_url}")

                try:
                    strongs = page.locator(
                        "div.recipe-cook-and-prep-details__item strong"
                    )
                    serves_text = await strongs.nth(0).inner_text()
                    serves_no = float(re.search(r"\d+", serves_text).group())
                    difficulty = await strongs.nth(1).inner_text()
                    logger.info(
                        f"Serves: {serves_no} and difficulty: {difficulty} for {recipe_url}"
                    )
                except Exception:
                    logger.warning(f"Serves or difficulty missing for {recipe_url}")

                try:
                    prep_time = (
                        await page.locator(
                            "div.recipe-cook-and-prep-details__item", has_text="Prep"
                        )
                        .locator("time")
                        .get_attribute("datetime")
                    )
                    logger.info(f"Prep time: {prep_time} for {recipe_url}")
                except Exception:
                    logger.warning(f"Prep time missing for {recipe_url}")

                try:
                    cook_time = (
                        await page.locator(
                            "div.recipe-cook-and-prep-details__item", has_text="Cook"
                        )
                        .locator("time")
                        .get_attribute("datetime")
                    )
                    logger.info(f"Cook time: {cook_time} for {recipe_url}")
                except Exception:
                    logger.warning(f"Cook time missing for {recipe_url}")

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
                            break
                    logger.info(f"Rating: {rating} for {recipe_url}")
                except Exception:
                    logger.warning(f"Rating missing for {recipe_url}")

                try:
                    description = await page.locator(
                        "#recipe-masthead-description-region p"
                    ).inner_text()
                    logger.info(f"Description: {description} for {recipe_url}")
                except Exception:
                    logger.warning(f"Description missing for {recipe_url}")

                try:
                    features = await page.locator(
                        ".post-header--masthead__tags-item"
                    ).all_inner_texts()
                except Exception:
                    logger.warning(f"Features missing for {recipe_url}")

                try:
                    ingredients = await page.locator(
                        "#ingredients-list li.ingredients-list__item"
                    ).all_inner_texts()
                except Exception:
                    logger.warning(f"Ingredients missing for {recipe_url}")

                try:
                    method = await page.locator(
                        ".method-steps__list-item .editor-content"
                    ).all_inner_texts()
                except Exception:
                    logger.warning(f"Method steps missing for {recipe_url}")

                try:
                    comments = await page.locator(
                        "article.reaction.reaction--parent div.mt-reset > p"
                    ).all_inner_texts()
                except Exception:
                    logger.warning(f"Comments missing for {recipe_url}")

                try:
                    nut_items = await page.locator(
                        "ul.nutrition-list li"
                    ).all_inner_texts()
                    nutrition_data = {}
                    for line in nut_items:
                        key = line.split()[0].rstrip(":").lower()
                        val = float(re.search(r"\d+(\.\d+)?", line).group())
                        if key in ("kcal", "calories"):
                            nutrition_data["calories"] = val
                        elif key == "fat":
                            nutrition_data["fat"] = val
                        elif key == "saturates":
                            nutrition_data["saturates"] = val
                        elif key == "carbs":
                            nutrition_data["carbs"] = val
                        elif key in ("sugar", "sugars"):
                            nutrition_data["sugar"] = val
                        elif key == "fibre":
                            nutrition_data["fibre"] = val
                        elif key == "protein":
                            nutrition_data["protein"] = val
                        elif key == "salt":
                            nutrition_data["salt"] = val
                    nutrition = Nutrition(**nutrition_data)
                except Exception:
                    logger.warning(f"Nutrition info missing for {recipe_url}")

                try:
                    img_url = await page.locator(
                        "section.post-header picture img"
                    ).get_attribute("src")
                    img_response = await context.request.get(img_url)
                    image_bytes = await img_response.body()
                except Exception:
                    logger.warning(f"Image missing for {recipe_url}")

                # write to DB
                recipe = Recipe(
                    id=item_id,
                    name=name,
                    cuisine_type=None,
                    serves_no=serves_no,
                    difficulty=difficulty,
                    prep_time=prep_time,
                    cook_time=cook_time,
                    rating=int(rating) if rating is not None else 0,
                    description=description,
                    features=features,
                    ingredients=ingredients,
                    method=method,
                    comments=comments,
                    nutrition=nutrition,
                    image=image_bytes,
                )
                recipes_table.add([recipe.model_dump()])

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
