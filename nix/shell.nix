{ inputs, ... }:
{
  imports = [ inputs.make-shell.flakeModules.default ];

  perSystem =
    { pkgs, ... }:
    {
      make-shells.default = {
        shellHook = ''
          export FLAKE_ROOT=$(git rev-parse --show-toplevel)
        '';
        packages = with pkgs; [
          natscli
        ];
      };
    };
}
