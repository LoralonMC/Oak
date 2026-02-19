"""
Shopkeepers Views
Paginated embed views for Discord interactions.
"""

import discord


class PaginatedEmbedView(discord.ui.View):
    """A Previous/Next button view for paginating through embeds."""

    def __init__(self, pages: list[discord.Embed], author_id: int):
        super().__init__(timeout=180.0)
        self.pages = pages
        self.author_id = author_id
        self.current_page = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= len(self.pages) - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your query.", ephemeral=True)
            return
        if self.current_page <= 0:
            await interaction.response.defer()
            return
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This isn't your query.", ephemeral=True)
            return
        if self.current_page >= len(self.pages) - 1:
            await interaction.response.defer()
            return
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass  # Message was deleted
