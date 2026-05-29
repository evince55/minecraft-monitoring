import discord
from discord import app_commands
from discord.ext import commands


class Status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="status", description="Live Minecraft server status")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            tps_data = await self.bot.prometheus.instant(
                "minecraft_tps_bucket_sum / minecraft_tps_bucket_count"
            )
            players_data = await self.bot.prometheus.instant(
                "increase(minecraft_play_time_ticks_total[5m]) > 0"
            )
            heap_data = await self.bot.prometheus.instant(
                "java_lang_Memory_HeapMemoryUsage_used / java_lang_Memory_HeapMemoryUsage_max"
            )
            uptime_data = await self.bot.prometheus.instant(
                'time() - process_start_time_seconds{job="minecraft-metrics"}'
            )

            tps = float(tps_data[0]["value"][1]) if tps_data else 0
            player_count = len(players_data)
            heap_pct = float(heap_data[0]["value"][1]) * 100 if heap_data else 0
            uptime_sec = float(uptime_data[0]["value"][1]) if uptime_data else 0
            days = int(uptime_sec // 86400)
            hours = int((uptime_sec % 86400) // 3600)

            embed = discord.Embed(
                title="Minecraft Server Status", color=0x722F37
            )
            embed.add_field(name="TPS", value=f"{tps:.1f}", inline=True)
            embed.add_field(
                name="Players Online", value=str(player_count), inline=True
            )
            embed.add_field(
                name="Heap", value=f"{heap_pct:.1f}%", inline=True
            )
            embed.add_field(
                name="Uptime", value=f"{days}d {hours}h", inline=True
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(
                f"Error fetching server status: {e}"
            )

    @app_commands.command(name="players", description="List online players")
    async def players(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            data = await self.bot.prometheus.instant(
                "increase(minecraft_play_time_ticks_total[5m]) > 0"
            )
            if not data:
                embed = discord.Embed(
                    title="Online Players",
                    description="No players online",
                    color=0x722F37,
                )
                await interaction.followup.send(embed=embed)
                return

            lines = []
            for r in data:
                player = r["metric"].get("player", "unknown")
                ticks = float(r["value"][1])
                mins = ticks / 20 / 60
                lines.append(f"**{player}** — {mins:.0f} min played")

            embed = discord.Embed(
                title=f"Online Players ({len(lines)})",
                description="\n".join(lines),
                color=0x722F37,
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"Error fetching players: {e}")

    @app_commands.command(name="tps", description="Current server TPS")
    async def tps(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            data = await self.bot.prometheus.instant(
                "minecraft_tps_bucket_sum / minecraft_tps_bucket_count"
            )
            tps = float(data[0]["value"][1]) if data else 0
            await interaction.followup.send(f"**Current TPS:** {tps:.1f}")
        except Exception as e:
            await interaction.followup.send(f"Error querying TPS: {e}")

    @app_commands.command(name="uptime", description="Server uptime")
    async def uptime(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            data = await self.bot.prometheus.instant(
                'time() - process_start_time_seconds{job="minecraft-metrics"}'
            )
            sec = float(data[0]["value"][1]) if data else 0
            days = int(sec // 86400)
            hours = int((sec % 86400) // 3600)
            await interaction.followup.send(
                f"**Server uptime:** {days}d {hours}h"
            )
        except Exception as e:
            await interaction.followup.send(f"Error querying uptime: {e}")


async def setup(bot):
    await bot.add_cog(Status(bot))
