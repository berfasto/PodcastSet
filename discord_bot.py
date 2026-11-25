"""Bot ligero para enviar niveles de voz en vivo por usuario desde Discord a PodcastSet.
Requisitos: pip install py-cord[voice] numpy
Uso: python discord_bot.py TU_TOKEN_DE_BOT
"""
import json, socket, sys
import numpy as np
import discord

PORT = 8765
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
bot = discord.Bot()

class LiveSink(discord.sinks.Sink):
    def write(self, data, user):
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(pcm * pcm))) if len(pcm) else 0.0
        db = 20.0 * np.log10(max(rms, 1e-6))
        lvl = float(np.clip((db + 60.0) / 60.0, 0.0, 1.0))
        msg = json.dumps({"user": str(user), "level": round(lvl, 3)}).encode("utf-8")
        sock.sendto(msg, ("127.0.0.1", PORT))

@bot.slash_command(description="Conecta PodcastSet a tu canal de voz actual")
async def conectar(ctx):
    if not ctx.author.voice:
        return await ctx.respond("Entra primero a un canal de voz.")
    vc = await ctx.author.voice.channel.connect()
    vc.start_recording(LiveSink(), lambda sink, *a: None)
    await ctx.respond(f"PodcastSet escuchando voces separadas en {ctx.author.voice.channel.name}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python discord_bot.py TU_TOKEN_DE_BOT")
        sys.exit(1)
    bot.run(sys.argv[1])
