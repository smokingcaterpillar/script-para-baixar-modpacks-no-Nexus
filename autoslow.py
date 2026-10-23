import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
import tkinter as tk
from tkinter import filedialog

import keyboard
import pyautogui
import pygetwindow as gw

# Garante que os prints (slow.png etc.) sejam achados na pasta do script
# (ou na pasta do .exe, quando empacotado com PyInstaller)
if getattr(sys, "frozen", False):
    PASTA_BASE = os.path.dirname(sys.executable)
else:
    PASTA_BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(PASTA_BASE)

# ---------- Registro de erros em arquivo ----------
ARQUIVO_ERROS = os.path.join(PASTA_BASE, "autoslow_erros.txt")
_ultimo_erro = {"texto": "", "quando": 0.0}


def registrar_erro(contexto, exc=None):
    """Anota o erro (com data/hora e detalhes) em autoslow_erros.txt."""
    try:
        detalhes = ""
        if exc is not None:
            detalhes = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
        texto = f"{contexto}\n{detalhes}"
        agora = time.time()
        # evita encher o arquivo se o mesmo erro se repetir a cada segundo
        if texto == _ultimo_erro["texto"] and agora - _ultimo_erro["quando"] < 60:
            return
        _ultimo_erro["texto"] = texto
        _ultimo_erro["quando"] = agora
        with open(ARQUIVO_ERROS, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {texto}\n")
    except Exception:
        pass


def _erro_em_thread(args):
    registrar_erro(f"Erro inesperado na thread {args.thread.name}", args.exc_value)


threading.excepthook = _erro_em_thread

# ================= CONFIGURAÇÕES =================
TITULOS = ["nexus", "mod page", "mods and community"]
IMAGENS = ["aviso.png", "slow.png"]
IMAGEM_DOWNLOAD = "slow.png"      # clique nessa imagem conta como 1 mod baixado
CONFIANCA = 0.8
PASSOS_SCROLL = 8
QTD_SCROLL = -400
ESPERA_APOS_CLIQUE = 5
PASTA_DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
AUTO_PARAR_SEGUNDOS = 300         # para sozinho após X s sem clicar (0 = nunca)
# =================================================

pyautogui.FAILSAFE = True         # mouse no canto superior esquerdo = parar

FILA_LOG = queue.Queue()


def log(msg):
    print(msg)
    FILA_LOG.put(msg)


for img in IMAGENS:
    if not os.path.exists(img):
        log(f"Aviso: {img} não encontrado, será ignorado.")
IMAGENS = [i for i in IMAGENS if os.path.exists(i)]


# ---------- Formatação ----------
def fmt_tempo(seg):
    seg = int(seg)
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_tamanho(b):
    gb = b / 1024 ** 3
    if gb >= 1:
        return f"{gb:.2f} GB"
    return f"{b / 1024 ** 2:.0f} MB"


# ---------- Pasta de downloads ----------
def listar_pasta(pasta):
    """Retorna {nome_base: tamanho}. Junta 'arq.crdownload' com 'arq'."""
    saida = {}
    try:
        for nome in os.listdir(pasta):
            caminho = os.path.join(pasta, nome)
            if not os.path.isfile(caminho):
                continue
            base = nome
            for ext in (".crdownload", ".part", ".tmp"):
                if base.lower().endswith(ext):
                    base = base[: -len(ext)]
            try:
                saida[base] = max(saida.get(base, 0), os.path.getsize(caminho))
            except OSError:
                pass
    except OSError:
        pass
    return saida


# ---------- Estado e estatísticas ----------
class Estado:
    def __init__(self):
        self.ligado = False
        self.sair = False
        self.pedido_toggle = False
        self.mods = 0
        self.tempo_acumulado = 0.0
        self.inicio = None
        self.ultimo_clique = time.time()
        self.pasta = PASTA_DOWNLOADS
        self.tamanhos = {}        # arquivos novos vistos: nome -> maior tamanho
        self.ignorar = set()      # arquivos que já existiam

    def tempo_total(self):
        t = self.tempo_acumulado
        if self.ligado and self.inicio:
            t += time.time() - self.inicio
        return t

    def bytes_total(self):
        return sum(self.tamanhos.values())

    def ligar(self):
        if self.ligado:
            return
        atuais = listar_pasta(self.pasta)
        self.ignorar |= {b for b in atuais if b not in self.tamanhos}
        self.inicio = time.time()
        self.ultimo_clique = time.time()
        self.ligado = True
        log("LIGADO")

    def desligar(self):
        if not self.ligado:
            return
        if self.inicio:
            self.tempo_acumulado += time.time() - self.inicio
        self.inicio = None
        self.ligado = False
        log("DESLIGADO")
        log(self.resumo())

    def zerar(self):
        self.mods = 0
        self.tempo_acumulado = 0.0
        self.tamanhos = {}
        self.ignorar = set()

    def resumo(self):
        linhas = [
            "---- RESUMO ----",
            f"Mods baixados: {self.mods}",
            f"Tempo ligado: {fmt_tempo(self.tempo_total())}",
            f"Baixado: {fmt_tamanho(self.bytes_total())}",
        ]
        if self.mods:
            linhas.append(f"Média por mod: {self.tempo_total() / self.mods:.0f} s")
        return "\n".join(linhas)


E = Estado()


# ---------- Automação ----------
def achar(img):
    try:
        return pyautogui.locateCenterOnScreen(img, confidence=CONFIANCA)
    except Exception:
        return None


def clicar_se_achar():
    for img in IMAGENS:
        pos = achar(img)
        if pos:
            pyautogui.click(pos.x, pos.y)
            E.ultimo_clique = time.time()
            if img == IMAGEM_DOWNLOAD:
                E.mods += 1
                log(f"Mod #{E.mods}: cliquei em {img}")
            else:
                log(f"Cliquei em {img}")
            return True
    return False


def achar_janela():
    for w in gw.getAllWindows():
        if w.title and w.visible and any(t in w.title.lower() for t in TITULOS):
            return w
    return None


def trazer_para_frente(w):
    ativa = gw.getActiveWindow()
    if ativa and ativa._hWnd == w._hWnd:
        return
    try:
        if w.isMinimized:
            w.restore()
        pyautogui.press("alt")   # ajuda o Windows a permitir a troca de foco
        w.activate()
        time.sleep(0.5)
    except Exception:
        pass


def passo():
    w = achar_janela()
    if not w:
        return
    trazer_para_frente(w)

    if clicar_se_achar():
        time.sleep(ESPERA_APOS_CLIQUE)
        return

    pyautogui.moveTo(w.left + w.width // 2, w.top + w.height // 2)
    for _ in range(PASSOS_SCROLL):
        if not E.ligado:
            return
        pyautogui.scroll(QTD_SCROLL)
        time.sleep(0.5)
        if clicar_se_achar():
            time.sleep(ESPERA_APOS_CLIQUE)
            return
    # não achou: volta ao topo e tenta de novo no próximo ciclo
    pyautogui.scroll(-QTD_SCROLL * PASSOS_SCROLL)


def trabalhar():
    while not E.sair:
        time.sleep(1)
        if not E.ligado:
            continue
        try:
            passo()
        except pyautogui.FailSafeException:
            log("Parado: mouse no canto da tela (failsafe).")
            E.desligar()
        except Exception as e:
            log(f"Erro: {e} (detalhes em autoslow_erros.txt)")
            registrar_erro("Erro no ciclo principal", e)

        if (
            E.ligado
            and AUTO_PARAR_SEGUNDOS
            and time.time() - E.ultimo_clique > AUTO_PARAR_SEGUNDOS
        ):
            log(f"Sem cliques há {AUTO_PARAR_SEGUNDOS} s. Parando sozinho.")
            E.desligar()


def monitorar():
    """Acompanha a pasta de downloads para somar o tamanho dos arquivos novos."""
    while not E.sair:
        try:
            if E.ligado:
                for base, tam in listar_pasta(E.pasta).items():
                    if base in E.ignorar:
                        continue
                    if tam > E.tamanhos.get(base, 0):
                        E.tamanhos[base] = tam
        except Exception as e:
            registrar_erro("Erro ao monitorar a pasta de downloads", e)
        time.sleep(0.3)


# ---------- Interface ----------
class App:
    def __init__(self, root):
        self.root = root
        root.title("AutoSlow")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.fechar)

        self.v_status = tk.StringVar(value="DESLIGADO")
        self.v_mods = tk.StringVar(value="0")
        self.v_tempo = tk.StringVar(value="00:00:00")
        self.v_tamanho = tk.StringVar(value="0 MB")
        self.v_pasta = tk.StringVar(value=E.pasta)

        frame = tk.Frame(root, padx=10, pady=8)
        frame.pack()

        tk.Label(frame, textvariable=self.v_status,
                 font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2)

        tk.Label(frame, text="Mods baixados:").grid(row=1, column=0, sticky="w")
        tk.Label(frame, textvariable=self.v_mods,
                 font=("Segoe UI", 10, "bold")).grid(row=1, column=1, sticky="e")
        tk.Label(frame, text="Tempo ligado:").grid(row=2, column=0, sticky="w")
        tk.Label(frame, textvariable=self.v_tempo,
                 font=("Segoe UI", 10, "bold")).grid(row=2, column=1, sticky="e")
        tk.Label(frame, text="Baixado (estimado):").grid(row=3, column=0, sticky="w")
        tk.Label(frame, textvariable=self.v_tamanho,
                 font=("Segoe UI", 10, "bold")).grid(row=3, column=1, sticky="e")

        self.btn = tk.Button(frame, text="Ligar (F8)", width=24, command=self.alternar)
        self.btn.grid(row=4, column=0, columnspan=2, pady=(8, 2))

        botoes = tk.Frame(frame)
        botoes.grid(row=5, column=0, columnspan=2)
        tk.Button(botoes, text="Zerar", width=11, command=self.zerar).pack(side="left", padx=2)
        tk.Button(botoes, text="Pasta de downloads", width=18,
                  command=self.escolher_pasta).pack(side="left", padx=2)

        tk.Label(frame, textvariable=self.v_pasta, fg="gray", wraplength=300,
                 font=("Segoe UI", 8)).grid(row=6, column=0, columnspan=2)

        self.texto = tk.Text(frame, height=9, width=42, state="disabled",
                             font=("Consolas", 9))
        self.texto.grid(row=7, column=0, columnspan=2, pady=(6, 0))

        # canto inferior direito da tela
        root.update_idletasks()
        x = root.winfo_screenwidth() - root.winfo_width() - 20
        y = root.winfo_screenheight() - root.winfo_height() - 80
        root.geometry(f"+{x}+{y}")

        self.atualizar()

    def alternar(self):
        if E.ligado:
            E.desligar()
        else:
            E.ligar()

    def zerar(self):
        if E.ligado:
            log("Desligue antes de zerar.")
            return
        E.zerar()
        log("Estatísticas zeradas.")

    def escolher_pasta(self):
        if E.ligado:
            log("Desligue antes de trocar a pasta.")
            return
        nova = filedialog.askdirectory(initialdir=E.pasta)
        if nova:
            E.pasta = nova
            E.zerar()
            self.v_pasta.set(nova)

    def fechar(self):
        E.desligar()
        E.sair = True
        self.root.destroy()

    def escrever(self, msg):
        self.texto.configure(state="normal")
        self.texto.insert("end", msg + "\n")
        self.texto.see("end")
        self.texto.configure(state="disabled")

    def atualizar(self):
        if E.pedido_toggle:
            E.pedido_toggle = False
            self.alternar()

        self.v_status.set("LIGADO" if E.ligado else "DESLIGADO")
        self.v_mods.set(str(E.mods))
        self.v_tempo.set(fmt_tempo(E.tempo_total()))
        self.v_tamanho.set(fmt_tamanho(E.bytes_total()))
        self.btn.configure(text="Desligar (F8)" if E.ligado else "Ligar (F8)")

        while True:
            try:
                self.escrever(FILA_LOG.get_nowait())
            except queue.Empty:
                break

        self.root.after(200, self.atualizar)


def main():
    try:
        keyboard.add_hotkey("F8", lambda: setattr(E, "pedido_toggle", True))
        threading.Thread(target=trabalhar, daemon=True).start()
        threading.Thread(target=monitorar, daemon=True).start()
        root = tk.Tk()
        root.report_callback_exception = (
            lambda tipo, valor, tb: registrar_erro("Erro na interface", valor)
        )
        App(root)
        log("Pronto. F8 liga/desliga.")
        root.mainloop()
    except Exception as e:
        registrar_erro("Erro ao iniciar/rodar o programa", e)
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "AutoSlow",
                f"Ocorreu um erro:\n{e}\n\nDetalhes em:\n{ARQUIVO_ERROS}",
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
