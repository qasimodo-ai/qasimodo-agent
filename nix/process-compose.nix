{inputs, lib, ...}: {
  imports = [inputs.process-compose-flake.flakeModule];

  perSystem = {config, pkgs, ...}: {
    make-shells.default.packages = [ config.packages.dev ];

    process-compose.dev.settings.processes = {
      nats-server.command = ''
        ${lib.getExe pkgs.nats-server} -js -DV -m 8222 -sd "$FLAKE_ROOT/nats-data"
      '';
    };
  };
}
