{
  description = "YAM — self-hosted YouTube video & playlist archiver";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };

        # Single source of dependency truth: the Python runtime plus all
        # application libraries, resolved from nixpkgs.
        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          fastapi
          uvicorn
          jinja2
          python-multipart
          sqlmodel
          yt-dlp
          httpx
        ]);

        # ffmpeg is needed at runtime for muxing streams into the container.
        runtimeDeps = [ pkgs.ffmpeg ];

        # The application: a wrapped uvicorn that serves yam.main:app with the
        # source on PYTHONPATH and ffmpeg on PATH.
        yam = pkgs.stdenv.mkDerivation {
          pname = "yam";
          version = "0.1.0";
          src = pkgs.lib.cleanSource ./.;
          dontConfigure = true;
          dontBuild = true;
          nativeBuildInputs = [ pkgs.makeWrapper ];
          installPhase = ''
            runHook preInstall
            mkdir -p $out/lib
            cp -r yam $out/lib/yam
            makeWrapper ${pythonEnv}/bin/uvicorn $out/bin/yam \
              --add-flags "yam.main:app --host 0.0.0.0 --port 8080" \
              --set PYTHONPATH $out/lib \
              --prefix PATH : ${pkgs.lib.makeBinPath runtimeDeps}
            runHook postInstall
          '';
        };

        # Container image, built from the same package set. Linux-only:
        # dockerTools cannot cross-build a Linux image from darwin without a
        # remote/linux builder, so we only expose it on Linux systems.
        dockerImage = pkgs.dockerTools.buildLayeredImage {
          name = "yam";
          tag = "latest";
          contents = [ yam pythonEnv pkgs.ffmpeg pkgs.cacert ];
          config = {
            Cmd = [ "${yam}/bin/yam" ];
            ExposedPorts = { "8080/tcp" = { }; };
            Env = [
              "MEDIA_DIR=/media"
              "DATA_DIR=/data"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            ];
          };
        };
      in
      {
        packages = {
          default = yam;
          yam = yam;
        } // pkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          docker = dockerImage;
        };

        formatter = pkgs.nixpkgs-fmt;

        devShells.default = pkgs.mkShell {
          packages = [ pythonEnv pkgs.ffmpeg pkgs.ruff pkgs.nixpkgs-fmt ];
          shellHook = ''
            export MEDIA_DIR="''${MEDIA_DIR:-$PWD/.local/media}"
            export DATA_DIR="''${DATA_DIR:-$PWD/.local/data}"
            echo "YAM devShell ready."
            echo "  run: uvicorn yam.main:app --reload --port 8080"
          '';
        };
      });
}
