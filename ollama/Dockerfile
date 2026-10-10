FROM ollama/ollama:latest

# The upstream image sets ENTRYPOINT ["/bin/ollama"], so a bare CMD would be
# parsed as an ollama subcommand. Reset it to run the startup script instead.
ENTRYPOINT []

COPY start-ollama.sh /usr/local/bin/start-ollama
RUN chmod +x /usr/local/bin/start-ollama

EXPOSE 11434

CMD ["start-ollama"]
