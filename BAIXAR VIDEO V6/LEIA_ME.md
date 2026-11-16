# Baixador de Vídeo V6

Abra com **BAIXAR_VIDEO.bat** (na 1ª vez ele prepara tudo sozinho; depois atualiza o yt-dlp uma vez por dia).

## Como usar
1. Cole um ou vários links na caixa (pode colar a conversa inteira do WhatsApp: ele acha os links).
2. Escolha a qualidade (★ guarda como padrão) e aperte **Baixar** (ou Ctrl+Enter).
3. Os vídeos vão para `videos_baixados` (troque em Configurações).

## Quando a página tem vários vídeos
- **Marcar na tela**: arraste sobre o vídeo que está tocando. A imagem é comparada com os vídeos da página e o certo ganha força.
- **Perguntar na dúvida** (ligado): se houver dois vídeos parecidos, aparece uma janela com as miniaturas para você escolher.
- Depois de baixar: **✓** = era esse (ele aprende); **✕** = não era esse (o arquivo errado vai para a Lixeira, você escolhe o certo e ele aprende).

## Quando nada baixa
- Vídeo com proteção (DRM) ou site fechado: **Gravar área** grava a área marcada com o som do computador.
- Vídeo que pede login: em Configurações, escolha o navegador em "Cookies do navegador" (feche o Opera/Chrome por alguns segundos).
- Bloqueio de país: Configurações → Rotas (proxy ou Tor).

## Como ele acha o vídeo
1. yt-dlp, quando o site tem extrator próprio (YouTube, Facebook, Instagram, TikTok...).
2. Senão, lê a página e lista todos os candidatos (25 jeitos de player); o **modelo aprendido** separa o vídeo principal de propaganda, prévia, vídeo de fundo e relacionados.
3. Em dúvida, abre a página no Edge escondido, aperta play e escuta a rede.
4. Baixa pelo motor certo (HTTP direto com retomada, yt-dlp, ffmpeg, streamlink), confere que o arquivo tem imagem e decodifica, e deixa em MP4.

Autoria: código escrito por Claude (Anthropic) a pedido do doug, em 15/11/2026, a partir das ideias das V1–V5 dele.
Motores de fora: yt-dlp, ffmpeg, streamlink, Microsoft Edge (Playwright). O aprendizado (pesos em `baixador/modelo_inicial.json`
e o que ele aprende com o seu uso em `dados/`) é próprio do programa.
Backup das versões antigas: `E:\BACKUPS_CLAUDE\BAIXAR_VIDEOS_V1_a_V5_2026-11-15.zip`.
Testes: `testes\` (gerador de páginas, servidor local de sites falsos, baterias).
