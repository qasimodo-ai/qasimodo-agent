{ inputs, ... }:
{
  perSystem =
    {
      config,
      pkgs,
      lib,
      ...
    }:
    let
      pyproject = lib.importTOML ../pyproject.toml;
      python = pkgs.python313;

      workspaceRoot = lib.fileset.toSource {
        root = ../.;
        fileset = lib.fileset.unions [
          ../pyproject.toml
          ../uv.lock
          ../src
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

      packages = {
        default = config.packages.qasimodo-agent;

        qasimodo-agent = mkApplication {
          venv = pythonSet.mkVirtualEnv "qasimodo-agent-env" workspace.deps.default;
          package = pythonSet.qasimodo-agent.overrideAttrs (old: {
            version = pyproject.project.version;
            __intentionallyOverridingVersion = true;
            meta = {
              mainProgram = "qasimodo-agent";
              maintainers = [ lib.maintainers.aciceri ];
            };
          });
        };
      };

      make-shells.default = {
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
}
