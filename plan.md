# Plan for Viralytics Updates

This plan outlines the steps to add region selection for video fetching, update the UI to allow overriding configuration values, and organize the interface with tabs.

## 1. `video_fetcher.py` - Region Selection for Trending Videos

*   **Modify `get_trending_videos` function:**
    *   Add a `region` parameter to the function signature.
    *   Update the TikTok API call to include the `region` parameter. This will likely involve using a different API endpoint or modifying the existing one to accept a region code (e.g., 'US', 'GB').
    *   Ensure the function still fetches trending/viral videos, which is the default behavior of the trending endpoint.

## 2. `index.html` - UI Rewrite with Overrides and Tabs

*   **Restructure with Tabs:**
    *   Create a tabbed interface to organize the different sections of the application (e.g., "Video Fetching", "Analysis", "Configuration").
    *   Use a simple HTML/CSS/JS tab implementation.
*   **Add Override Forms:**
    *   In the "Configuration" tab, add input fields for users to override the following values:
        *   `API_KEY` (from `.env`)
        *   `MS_TOKEN` (from `.env`)
        *   `database_name` (from `config.yaml`)
        *   `table_name` (from `config.yaml`)
        *   `model` (from `config.yaml`)
        *   `temperature` (from `config.yaml`)
        *   `max_tokens` (from `config.yaml`)
    *   These fields will be optional. If left blank, the application will use the values from the `.env` and `config.yaml` files.
*   **Update Main Form:**
    *   Add a dropdown menu for region selection (e.g., "US", "GB", "DE", "FR") in the "Video Fetching" tab.
    *   Ensure the form submission sends all the necessary data, including the new override values and the selected region, to the backend.

## 3. `main.py` - Backend Logic for Overrides

*   **Update `index` route:**
    *   Modify the `index` route to handle POST requests with the new form data.
    *   Retrieve the override values from the form submission.
    *   If override values are provided, use them. Otherwise, load the values from the `.env` and `config.yaml` files as usual.
    *   Pass the selected `region` to the `get_trending_videos` function.
    *   Pass the override values to the other modules (`trend_analyzer`, `video_analyzer`, etc.) as needed.

## 4. Implementation Order

1.  Create `plan.md`.
2.  Modify `video_fetcher.py` to include region selection.
3.  Rewrite `index.html` with the new tabbed layout and override forms.
4.  Update `main.py` to handle the new form data and pass the values to the appropriate functions.
5.  Test the application to ensure all the new features work correctly.
