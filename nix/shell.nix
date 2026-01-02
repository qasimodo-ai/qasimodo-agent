{ inputs, lib, ... }:
{
  imports = [ inputs.make-shell.flakeModules.default ];

  perSystem =
    { pkgs, ... }:
    {
      make-shells.default = {
        shellHook = ''
          export FLAKE_ROOT=$(git rev-parse --show-toplevel)
        ''
        + lib.optionalString pkgs.stdenv.isLinux ''
          export QASIMODO_AGENT_CHROMIUM_EXECUTABLE=${lib.getExe pkgs.chromium}
        '';
        packages = with pkgs; [
          natscli
        ];
      };
    };
}
