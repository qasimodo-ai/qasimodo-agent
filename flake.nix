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

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
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
        {
          config,
          pkgs,
          lib,
          ...
        }:
        let
          pyproject = lib.importTOML ./pyproject.toml;

          python = pkgs.python313;

          workspaceRoot = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./pyproject.toml
              ./uv.lock
              ./src
            ];
          };

          workspace = inputs.uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };

          overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

          pythonSet = (pkgs.callPackage inputs.pyproject-nix.build.packages { inherit python; }).overrideScope (
            lib.composeManyExtensions [
              inputs.pyproject-build-systems.overlays.default
              overlay
            ]
          );

          inherit (pkgs.callPackages inputs.pyproject-nix.build.util { }) mkApplication;
        in
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

          packages = {
            default = config.packages.qasimodo-agent;

            qasimodo-agent = mkApplication {
              venv = pythonSet.mkVirtualEnv "qasimodo-agent-env" workspace.deps.default;
              package = pythonSet.qasimodo-agent.overrideAttrs (old: {
                version = pyproject.project.version;
                __intentionallyOverridingVersion = true;
              });
            };
          };

          devShells.default = pkgs.mkShell {
            packages = with pkgs; [
              uv
              pythonSet.python
              playwright-driver.browsers
            ];
            shellHook = ''
              ${config.pre-commit.installationScript}
              export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
              export PLAYWRIGHT_BROWSERS_PATH="${pkgs.playwright-driver.browsers}"
              export PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true
            '';
          };
        };
    };
}
