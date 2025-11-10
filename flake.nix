{
  description = "QAsimodo agent";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    flake-parts.url = "github:hercules-ci/flake-parts";

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "";
    };

    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs = {
        nixpkgs.follows = "";
        flake-compat.follows = "";
        gitignore.follows = "";
      };
    };
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      imports = with inputs; [
        treefmt-nix.flakeModule
        git-hooks.flakeModule
      ];

      perSystem =
        { config, pkgs, ... }:
        {
          treefmt = {
            programs = {
              nixfmt = {
                enable = true;
                width = 120;
              };
              nixf-diagnose.enable = true;
              ruff-format = {
                enable = true;
                lineLength = 120;
              };
              ruff-check.enable = true;
              yamlfmt.enable = true;
            };
          };

          pre-commit.settings.hooks.treefmt.enable = true;

          devShells.default = pkgs.mkShell {
            packages = with pkgs; [
              uv
              python313
            ];
            shellHook = ''
              ${config.pre-commit.installationScript}
            '';
          };
        };
    };
}
