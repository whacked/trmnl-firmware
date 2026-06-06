# Dev shell for the BYOS reference server.
#   nix-shell shell.nix --run 'python server.py'
# (server.py also has a nix-shell shebang, so ./server.py works on its own.)
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = [ (pkgs.python3.withPackages (ps: with ps; [ pillow fastapi uvicorn pydantic pyyaml pytest httpx ])) ];
}
